// 도면 변경 감지 → (디바운스) → 전체 스냅샷 재추출 → 엔진 검사 → 표시.
//
// 동기화 원칙: 이벤트는 '다시 검사해야 한다'는 신호(dirty)로만 쓴다. 무엇이 바뀌었는지는 엔진이
// 이전 스냅샷과 비교해서 계산한다. 그래서 Undo/Redo/복사/레이어 변경 어떤 경로로 바뀌어도
// 다음 검사의 스냅샷이 곧 진실이 되고, 결과 캐시도 엔진에서 입력 해시로 맞춰진다.
//
// 마우스 오버(기능 3): 커서 아래 객체의 요소 판정(엔진 elements)을 툴팁으로(✔ 충족 / ✖ 미충족 / ⚠ 확인 필요)
// 보여 주고 상태 색으로 강조한다. AutoCAD가 집어 준 객체가 없으면 공간 인덱스(HitIndex)로 커서 근처 선분·감싸는
// 닫힌 도형을 찾는다. 계산은 40 ms에 한 번(throttle) — 그 사이 이벤트는 직전 결과를 재사용.
// 저장(기능 1): 도면을 저장하면 다음 Idle에 버전 기록 요청(VersionRequested)을 낸다.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text;
using Autodesk.AutoCAD.ApplicationServices;
using Autodesk.AutoCAD.DatabaseServices;
using Autodesk.AutoCAD.EditorInput;
using AcApp = Autodesk.AutoCAD.ApplicationServices.Application;

namespace GaRuleCheck
{
    public sealed class DocWatcher : IDisposable
    {
        private static readonly HashSet<string> RecheckCommands = new(StringComparer.OrdinalIgnoreCase)
        {
            "DWGPROPS", "UNDO", "U", "REDO", "MREDO", "LAYER", "-LAYER", "RENAME", "PROPERTIES",
        };
        private const int DebounceMs = 300;

        public Document Doc { get; }
        public CheckResponse? Last { get; private set; }
        public event Action<DocWatcher>? Checked;
        public event Action<DocWatcher, string>? Failed;
        /// 도면이 저장된 뒤(다음 Idle) — 버전 기록 창을 띄우라는 신호
        public event Action<DocWatcher>? VersionRequested;

        private bool _dirty = true;
        private DateTime _lastChange = DateTime.MinValue;
        private bool _busy;
        public bool Busy => _busy;
        /// GA_ 레이어·선박 정보가 바뀌었는데 아직 다시 검사하지 않았음(마지막 결과가 지금 도면보다 오래됨)
        public bool Dirty => _dirty;
        /// 마지막 검사가 엔진 오류로 실패했음(배지 '엔진 응답 없음'용)
        public bool LastFailed { get; private set; }
        private HashSet<string> _lastHandles = new();
        private HashSet<string> _mappedLayers = new();   // GARC_LAYER_ROLES의 레이어(실도면 연결) — 바뀌면 다시 검사
        private Dictionary<string, string> _tips = new();   // handle → 마우스 툴팁 문구
        private Dictionary<string, List<ElementCheckDto>> _elements = new();
        private Dictionary<string, (List<double[]> pts, bool closed, List<double>? bulges)> _shapes = new();   // 마지막 스냅샷의 도면 단위 좌표
        private HitIndex? _hit;
        private readonly Stopwatch _hoverClock = Stopwatch.StartNew();
        private string? _hoverHandle;
        private bool _versionPending;
        public RawDrawing? LastRaw { get; private set; }

        public DocWatcher(Document doc)
        {
            Doc = doc;
            var db = doc.Database;
            db.ObjectAppended += OnObject;
            db.ObjectModified += OnObject;
            db.ObjectErased += OnErased;
            doc.CommandEnded += OnCommandEnded;
            doc.Editor.PointMonitor += OnPointMonitor;
            db.SaveComplete += OnSaveComplete;
            AcApp.Idle += OnIdle;
        }

        public void Dispose()
        {
            AcApp.Idle -= OnIdle;
            try
            {
                var db = Doc.Database;
                db.ObjectAppended -= OnObject;
                db.ObjectModified -= OnObject;
                db.ObjectErased -= OnErased;
                Doc.CommandEnded -= OnCommandEnded;
                Doc.Editor.PointMonitor -= OnPointMonitor;
                db.SaveComplete -= OnSaveComplete;
            }
            catch { /* 문서가 이미 닫힘 */ }
        }

        public void MarkDirty()
        {
            _dirty = true;
            _lastChange = DateTime.UtcNow;
        }

        private void OnObject(object sender, ObjectEventArgs e) => Consider(e.DBObject);
        private void OnErased(object sender, ObjectErasedEventArgs e) => Consider(e.DBObject);

        private void Consider(DBObject obj)
        {
            try
            {
                if (obj is Xrecord) { MarkDirty(); return; }   // 역할 지정(GARC_ROLES)이 바뀜 — 판단은 엔진
                if (obj is not Entity ent) return;
                // 감시 레이어에 있거나, 직전 스냅샷에 있던 객체(= 다른 레이어로 옮겨진 경우)면 다시 검사.
                // 'GA'로 시작하는 다른 레이어(GA-OBST·전각 ＧＡ＿OBST 같은 비슷한 이름)도 신호로 쓴다 — 판단은 엔진(layer_counts)이 한다.
                if (Contract.IsWatchedLayer(ent.Layer) || _lastHandles.Contains(ent.Handle.ToString())
                    || StartsWithGa(ent.Layer) || _mappedLayers.Contains(ent.Layer.Trim().ToUpperInvariant()))
                    MarkDirty();
            }
            catch
            {
                MarkDirty(); // 판단이 안 되면 보수적으로 다시 검사
            }
        }

        /// 엔진의 유사 레이어 비교와 같은 정규화(NFKC·대문자·ASCII 영숫자만) 후 'GA'로 시작하는지. 재검사 신호용(판단 아님).
        private static bool StartsWithGa(string layer)
        {
            var n = new string(layer.Normalize(NormalizationForm.FormKC).ToUpperInvariant().Where(char.IsAsciiLetterOrDigit).ToArray());
            return n.StartsWith("GA", StringComparison.Ordinal);
        }

        /// 판정 대상 객체 위에 커서를 올리면 요소 판정(충족·미충족·확인 필요)을 툴팁으로, 객체를 상태 색으로 강조.
        private void OnPointMonitor(object sender, PointMonitorEventArgs e)
        {
            if (_tips.Count == 0) return;
            try
            {
                if (_hoverClock.ElapsedMilliseconds >= 40)   // throttle: 40 ms에 한 번만 다시 찾는다
                {
                    _hoverClock.Restart();
                    var h = FindHovered(e);
                    if (h != _hoverHandle)
                    {
                        _hoverHandle = h;
                        if (h != null && _shapes.TryGetValue(h, out var sh) && Overlay.Enabled)
                            Overlay.ElementHover(Doc, sh.pts, sh.closed, WorstStatus(h), sh.bulges);
                        else
                            Overlay.ElementHover(Doc, null, false, "");
                    }
                }
                if (_hoverHandle != null && _tips.TryGetValue(_hoverHandle, out var tip)) e.AppendToolTipText(tip);
            }
            catch { /* 툴팁·강조는 부가 기능 — 실패해도 무시 */ }
        }

        private string? FindHovered(PointMonitorEventArgs e)
        {
            var paths = e.Context.GetPickedEntities();
            if (paths != null)
                foreach (var path in paths)
                    foreach (var id in path.GetObjectIds())
                    {
                        var h = id.Handle.ToString();
                        if (_tips.ContainsKey(h)) return h;
                    }
            if (_hit == null) return null;
            // AutoCAD가 집지 못한 경우(닫힌 도형 안쪽 등): 공간 인덱스로 커서 근처(화면 6 px) 선분 → 감싸는 도형
            var p = e.Context.RawPoint;
            return _hit.Hit(p.X, p.Y, 6 * Overlay.UnitsPerPixel(Doc));
        }

        public string WorstStatus(string handle) =>
            _elements.TryGetValue(handle, out var list) && list.Count > 0
                ? list.OrderBy(x => Theme.StatusRank(x.Status)).First().Status
                : "unknown";

        public (List<double[]> pts, bool closed, List<double>? bulges)? ShapeOf(string handle) =>
            _shapes.TryGetValue(handle, out var s) ? s : null;

        /// 미충족 요소 수, 확인 필요 요소 수(로고 배지용)
        public (int fail, int unknown) ElementCounts()
        {
            int f = 0, u = 0;
            foreach (var list in _elements.Values)
                foreach (var x in list)
                    if (x.Status == "fail") f++;
                    else if (x.Status == "unknown") u++;
            return (f, u);
        }

        private void BuildTips(RawDrawing raw)
        {
            var tips = new Dictionary<string, string>();
            var elements = new Dictionary<string, List<ElementCheckDto>>();
            if (Last != null)
            {
                foreach (var r in Last.Results)
                    foreach (var x in r.Elements)
                    {
                        if (!elements.TryGetValue(x.ElementId, out var list)) elements[x.ElementId] = list = new List<ElementCheckDto>();
                        list.Add(x);
                    }
                foreach (var (h, list) in elements)
                {
                    var parts = list.OrderBy(x => Theme.StatusRank(x.Status)).Select(x =>
                    {
                        var t = $"{Theme.ElementMark(x.Status)} — {x.RuleName}\n  기준 {x.Required}\n  실제 {x.Actual}";
                        if (!string.IsNullOrEmpty(x.Diff)) t += $"\n  차이 {x.Diff}";
                        if (!string.IsNullOrEmpty(x.Guide)) t += $"\n  {(x.Status == "unknown" ? "사유" : "가이드")} {x.Guide}";
                        return t;
                    });
                    tips[h] = $"[{list[0].RoleKo} {h}]\n" + string.Join("\n\n", parts);
                }
                // 요소 판정이 없는 규칙 대상(예: 엔진이 elements를 못 낸 경우)도 예전처럼 규칙 문구로
                foreach (var r in Last.Results)
                {
                    if (r.Status is not ("fail" or "review")) continue;
                    foreach (var h in r.Targets)
                        if (!tips.ContainsKey(h))
                            tips[h] = $"[{r.StatusKo}] {r.TitleKo}\n{r.MessageKo}" + (string.IsNullOrEmpty(r.SuggestionKo) ? "" : "\n제안: " + r.SuggestionKo);
                }
            }
            var shapes = new Dictionary<string, (List<double[]> pts, bool closed, List<double>? bulges)>();
            foreach (var en in raw.Entities)
                shapes[en.Handle] = (en.Points, en.Closed == true, en.Bulges);
            _tips = tips;
            _elements = elements;
            _shapes = shapes;
            _hit = new HitIndex(raw.Entities.Where(x => tips.ContainsKey(x.Handle))
                                   .Select(x => (x.Handle, (IReadOnlyList<double[]>)x.Points, x.Closed == true)));
            _hoverHandle = null;
            Overlay.ElementHover(Doc, null, false, "");   // 다시 검사하면 이전 결과의 hover 강조를 지운다
        }

        private void ClearTips()
        {
            _tips = new();
            _elements = new();
            _shapes = new();
            _hit = null;
            _hoverHandle = null;
            Overlay.ElementHover(Doc, null, false, "");
        }

        private void OnSaveComplete(object sender, DatabaseIOEventArgs e)
        {
            // 자동 저장(.sv$)·임시 파일은 버전으로 남기지 않는다
            var f = e.FileName ?? "";
            var ext = Path.GetExtension(f).ToLowerInvariant();
            if (ext is ".sv$" or ".bak" or ".tmp" || f.Contains(Path.GetTempPath(), StringComparison.OrdinalIgnoreCase)) return;
            _versionPending = true;
        }

        private void OnCommandEnded(object sender, CommandEventArgs e)
        {
            if (RecheckCommands.Contains(e.GlobalCommandName)) MarkDirty();
        }

        private void OnIdle(object? sender, EventArgs e)
        {
            if (_versionPending && !_busy && AcApp.DocumentManager.MdiActiveDocument == Doc && string.IsNullOrEmpty(Doc.CommandInProgress))
            {
                _versionPending = false;
                VersionRequested?.Invoke(this);
                return;
            }
            if (!_dirty || _busy) return;
            if (AcApp.DocumentManager.MdiActiveDocument != Doc) return;
            if (!string.IsNullOrEmpty(Doc.CommandInProgress)) return;
            if ((DateTime.UtcNow - _lastChange).TotalMilliseconds < DebounceMs) return;
            RunCheck();
        }

        public void RunCheck()
        {
            _busy = true;
            _dirty = false;
            try
            {
                RawDrawing raw;
                using (Doc.LockDocument())
                {
                    raw = Extractor.Extract(Doc);
                }
                // 펼친 블록의 하위 객체는 원래 블록 handle로도 기억한다(블록을 옮기면 다시 검사)
                _lastHandles = raw.Entities.Select(x => x.Handle).Concat(raw.Entities.Select(x => Extractor.BaseHandle(x.Handle))).ToHashSet();
                _mappedLayers = Contract.LayerRoleNames(raw.Props);
                LastRaw = raw;
                Last = EngineClient.Check(Extractor.DocId(Doc), raw);
                BuildTips(raw);
                using (Doc.LockDocument())
                {
                    Overlay.Show(Doc, Last);
                }
                LastFailed = false;
                Checked?.Invoke(this);
            }
            catch (EngineException ex)
            {
                Last = null;
                LastFailed = true;
                ClearTips();
                Overlay.Clear();
                Failed?.Invoke(this, ex.Message);
            }
            catch (Exception ex)
            {
                Last = null;
                LastFailed = true;
                ClearTips();   // 이전 결과의 툴팁·강조가 남지 않게
                Overlay.Clear();
                Failed?.Invoke(this, "플러그인 오류: " + ex.Message);
            }
            finally
            {
                _busy = false;
            }
        }
    }
}
