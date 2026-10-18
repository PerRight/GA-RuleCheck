// 진입점과 명령. 리본 'GA RuleCheck' 탭의 버튼도 아래 명령을 실행한다.
//   GARC        검사 시작(팔레트 + 현재 도면 감시)        GARCCHECK  지금 다시 검사
//   GARCOFF     감시 중지 + 표시 지우기                    GARCHEALTH 엔진 연결 확인
//   GARCSETUP   GA_ 레이어·라벨 스타일·특성 틀 만들기      GARCSHIP   선박 정보 탭 열기
//   GARCEYE     조종위치(GA_EYE 점) 지정                   GARCPICK   AP/FP/기선을 도면에서 지정
//   GARCTOGGLE  도면 표시 켜기/끄기                        GARCREPORT HTML 검토 리포트 저장·열기
//   GARCLARGE   라벨 확대(라벨 글자·선 두께·표식 크기 2.5배 — 판정·도면은 그대로)
//   GARCVERSION 지금 상태를 버전으로 기록(저장할 때도 자동으로 묻는다)   GARCCOMPARE 버전 비교 창
//   GARCPANEL   떠 있는 로고 패널 열기/닫기(Ctrl+.)                     GARCLOGO   로고 보이기/숨기기
//   GARCLINK    실도면 연결 마법사(치수표·축척 → 측면도 영역 → AP/FP/기선 → 역할 지정 → 조종위치)   [LinkWizard.cs]
//   GARCROLE    고른 객체에 역할(선체·장애물·충돌격벽·구상선수·조종위치) 붙이기/해제   GARCROLESHOW 판정에 쓰인 객체 역할별 색 표시
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using Autodesk.AutoCAD.ApplicationServices;
using Autodesk.AutoCAD.DatabaseServices;
using Autodesk.AutoCAD.EditorInput;
using Autodesk.AutoCAD.Geometry;
using Autodesk.AutoCAD.Runtime;
using Autodesk.AutoCAD.Windows;
using AcApp = Autodesk.AutoCAD.ApplicationServices.Application;

[assembly: ExtensionApplication(typeof(GaRuleCheck.PluginApp))]
[assembly: CommandClass(typeof(GaRuleCheck.Commands))]

namespace GaRuleCheck
{
    public sealed class PluginApp : IExtensionApplication
    {
        public void Initialize()
        {
            AcApp.DocumentManager.MdiActiveDocument?.Editor.WriteMessage(
                "\nGA-RuleCheck 로드됨 — 리본 'GA RuleCheck' 탭 또는 GARC 명령으로 시작하세요.\n");
            AcApp.DocumentManager.DocumentToBeDestroyed += (_, e) => Commands.Forget(e.Document);
            AcApp.DocumentManager.DocumentActivated += (_, e) => Commands.OnActivated(e.Document);
            try { RibbonUi.Install(); } catch { /* 리본 없는 환경(콘솔 등) */ }
            StatusTray.Install();
            StatusTray.Clicked += Commands.OpenFromTray;
            // 떠 있는 로고는 메인 창이 준비된 뒤(첫 Idle)에 만든다
            AcApp.Idle += InitFloatingOnce;
        }

        private static void InitFloatingOnce(object? sender, EventArgs e)
        {
            AcApp.Idle -= InitFloatingOnce;
            try { Commands.InitFloating(); } catch { /* 로고는 부가 기능 */ }
        }

        public void Terminate()
        {
            Commands.DetachAll();
            StatusTray.Remove();
            FloatingUi.Shutdown();
        }
    }

    public sealed partial class Commands
    {
        private static readonly Dictionary<Document, DocWatcher> Watchers = new();
        private static readonly Dictionary<Document, CompareForm> CompareForms = new();
        private static PaletteSet? _palette;
        private static ResultPanel? _panel;

        /// 열려 있는 결과 패널 전부(도킹 팔레트 + 떠 있는 로고 패널) — 같은 내용으로 함께 갱신한다.
        private static IEnumerable<ResultPanel> Panels()
        {
            if (_panel != null && !_panel.IsDisposed) yield return _panel;
            if (FloatingUi.Panel is { IsDisposed: false } fp) yield return fp;
        }

        private static void ForPanels(Action<ResultPanel> act)
        {
            foreach (var p in Panels().ToList()) act(p);
        }

        private static Document? Active => AcApp.DocumentManager.MdiActiveDocument;
        private static string DocName(Document d) => Path.GetFileName(d.Name);

        // ── 검사 ────────────────────────────────────────────────────────────

        [CommandMethod("GARC")]
        public void Start()
        {
            var doc = Active;
            if (doc == null) return;
            EnsurePalette();
            DrawingProps.EnsureSetup(doc, props: false);   // 라벨 스타일·레이어가 없으면 만든다
            if (!EngineClient.EnsureRunning())
                doc.Editor.WriteMessage("\nGA-RuleCheck: 엔진 서버를 켤 수 없습니다. engine 폴더에서 'python -m garc serve'를 실행하세요.\n");
            Attach(doc).MarkDirty();
            _panel!.Ship.Load(DrawingProps.Read(doc.Database));
            if (!DrawingProps.IsConfigured(doc.Database))
            {
                _panel.ShowTab("ship");
                doc.Editor.WriteMessage("\nGA-RuleCheck: 선박 정보(LOA 등)가 비어 있습니다. 팔레트 '선박 정보' 탭에서 입력하세요.\n");
            }
            doc.Editor.WriteMessage("\nGA-RuleCheck: 감시 시작. GA_ 레이어가 바뀌면 자동으로 다시 검사합니다.\n");
        }

        [CommandMethod("GARCCHECK")]
        public void CheckNow()
        {
            var doc = Active;
            if (doc == null) return;
            EnsurePalette();
            EngineClient.EnsureRunning();
            Attach(doc).RunCheck();
        }

        [CommandMethod("GARCOFF")]
        public void Stop()
        {
            var doc = Active;
            if (doc != null) Detach(doc);
            ForPanels(p => p.SetIdle("감시 중지됨", doc != null ? DocName(doc) : null));
            FloatingUi.SetStatus("idle", 0, 0);   // 로고 배지에 이전 개수가 남지 않게
        }

        [CommandMethod("GARCHEALTH")]
        public void Health()
        {
            var ed = Active?.Editor;
            try { ed?.WriteMessage("\n엔진 응답: " + EngineClient.Health() + "\n"); ForPanels(p => p.SetEngine(true)); }
            catch (EngineException ex) { ed?.WriteMessage("\n" + ex.Message + "\n"); ForPanels(p => p.SetEngine(false)); }
        }

        // ── 입력 ────────────────────────────────────────────────────────────

        [CommandMethod("GARCSETUP")]
        public void Setup()
        {
            var doc = Active;
            if (doc == null) return;
            DrawingProps.EnsureSetup(doc, props: true);
            var props = DrawingProps.Read(doc.Database);
            ForPanels(p => p.Ship.Load(props));
            doc.Editor.WriteMessage(
                "\nGA_ 레이어 5개, 라벨 문자 스타일(GARC_LABEL), GARC_* 특성 틀을 만들었습니다." +
                "\n좌표 규약: 측면도, X = 선수 방향 +, Y = 위 +. 선박 정보는 팔레트 '선박 정보' 탭에서 입력.\n");
        }

        [CommandMethod("GARCSHIP")]
        public void Ship()
        {
            var doc = Active;
            if (doc == null) return;
            EnsurePalette();
            _panel!.Ship.Load(DrawingProps.Read(doc.Database));
            _panel.ShowTab("ship");
        }

        [CommandMethod("GARCEYE")]
        public void Eye()
        {
            var doc = Active;
            if (doc == null) return;
            var ed = doc.Editor;
            var res = ed.GetPoint(new PromptPointOptions("\n조종위치(선교 갑판 높이)를 클릭: "));
            if (res.Status != PromptStatus.OK) return;
            PlaceEye(doc, res.Value.TransformBy(ed.CurrentUserCoordinateSystem));
        }

        /// GA_EYE 점 하나를 pt(WCS)에 둔다 — 1개면 이동(handle 유지), 없으면 만든다. 실도면 연결 마법사도 쓴다.
        internal static void PlaceEye(Document doc, Point3d pt)
        {
            var ed = doc.Editor;
            DrawingProps.EnsureSetup(doc, props: false);
            var db = doc.Database;
            using (var tr = db.TransactionManager.StartTransaction())
            {
                var ms = (BlockTableRecord)tr.GetObject(SymbolUtilityServices.GetBlockModelSpaceId(db), OpenMode.ForWrite);
                var eyes = new List<DBPoint>();
                foreach (ObjectId id in ms)
                    if (tr.GetObject(id, OpenMode.ForRead) is DBPoint p && p.Layer.Equals("GA_EYE", StringComparison.OrdinalIgnoreCase))
                        eyes.Add(p);
                if (eyes.Count == 1)
                {
                    eyes[0].UpgradeOpen();
                    eyes[0].Position = pt;   // 기존 점 이동 → handle 유지(이력에 '수정'으로 남음)
                }
                else
                {
                    var p = new DBPoint(pt) { Layer = "GA_EYE" };
                    ms.AppendEntity(p);
                    tr.AddNewlyCreatedDBObject(p, true);
                    if (eyes.Count > 1) ed.WriteMessage($"\n주의: GA_EYE 점이 {eyes.Count + 1}개입니다. 하나만 남기세요(엔진이 '검토 불가'로 표시).");
                }
                tr.Commit();
            }
            if (db.Pdmode == 0) db.Pdmode = 35;   // 점이 보이도록 (도면 설정 변경, Undo 가능)
            ed.WriteMessage("\n조종위치를 지정했습니다.\n");
        }

        [CommandMethod("GARCPICK")]
        public void Pick()
        {
            var doc = Active;
            if (doc == null) return;
            var ed = doc.Editor;
            var kw = new PromptKeywordOptions("\n지정할 항목 [AP/FP/BASE]: ");
            kw.Keywords.Add("AP"); kw.Keywords.Add("FP"); kw.Keywords.Add("BASE");
            var k = ed.GetKeywords(kw);
            if (k.Status != PromptStatus.OK) return;
            double? scale = Watchers.TryGetValue(doc, out var w) ? w.Last?.UnitsScale : null;
            if (scale is not double s || s <= 0)
            {
                ed.WriteMessage("\n도면 단위를 아직 모릅니다. 먼저 GARC로 한 번 검사하세요(단위 해석은 엔진이 합니다).\n");
                return;
            }
            var label = k.StringResult switch { "AP" => "AP(선미수선)", "FP" => "FP(선수수선)", _ => "기선(baseline)" };
            var res = ed.GetPoint(new PromptPointOptions($"\n{label} 위치를 클릭: "));
            if (res.Status != PromptStatus.OK) return;
            var pt = res.Value.TransformBy(ed.CurrentUserCoordinateSystem);
            var (key, value) = k.StringResult switch
            {
                "AP" => ("GARC_X_AP", pt.X * s),
                "FP" => ("GARC_X_FP", pt.X * s),
                _ => ("GARC_Z_BASE", pt.Y * s),
            };
            DrawingProps.Write(doc.Database, new Dictionary<string, string> { [key] = DrawingProps.M(value) });
            ForPanels(p => p.Ship.SetField(key, DrawingProps.M(value)));
            w?.MarkDirty();
            ed.WriteMessage($"\n{key} = {DrawingProps.M(value)} m\n");
        }

        // ── 표시·기록 ───────────────────────────────────────────────────────

        [CommandMethod("GARCTOGGLE")]
        public void Toggle()
        {
            var doc = Active;
            Overlay.Enabled = !Overlay.Enabled;
            ForPanels(p => p.SetOverlayState(Overlay.Enabled));
            Redraw(doc);
            doc?.Editor.WriteMessage($"\n도면 표시 {(Overlay.Enabled ? "켜짐" : "꺼짐")}\n");
        }

        [CommandMethod("GARCLARGE")]
        public void LargeLabels()
        {
            var doc = Active;
            Overlay.Scale = Overlay.Scale > 1.0 ? 1.0 : 2.5;
            ForPanels(p => p.SetLargeLabelsState(Overlay.Scale > 1.0));
            Redraw(doc);
            doc?.Editor.WriteMessage($"\n라벨 확대 {(Overlay.Scale > 1.0 ? "켜짐 (라벨 글자·선 두께·표식 크기 2.5배, 판정은 그대로)" : "꺼짐")}\n");
        }

        [CommandMethod("GARCREPORT")]
        public void Report()
        {
            var doc = Active;
            if (doc == null) return;
            if (!Watchers.TryGetValue(doc, out var w) || w.Last == null)
            {
                doc.Editor.WriteMessage("\n먼저 GARC로 검사하세요.\n");
                return;
            }
            try
            {
                var html = EngineClient.Report(Extractor.DocId(doc), DocName(doc));
                var dir = Path.GetDirectoryName(doc.Name);
                if (string.IsNullOrEmpty(dir) || !Directory.Exists(dir))
                    dir = Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments);
                var file = Path.Combine(dir, $"{Path.GetFileNameWithoutExtension(doc.Name)}_GA검토_{DateTime.Now:yyyyMMdd_HHmm}.html");
                File.WriteAllText(file, html, new System.Text.UTF8Encoding(false));
                Process.Start(new ProcessStartInfo(file) { UseShellExecute = true });
                doc.Editor.WriteMessage($"\n리포트 저장: {file}\n");
            }
            catch (System.Exception ex)
            {
                doc.Editor.WriteMessage("\n리포트 실패: " + ex.Message + "\n");
            }
        }

        // ── 버전 기록·비교 (기능 1) ─────────────────────────────────────────

        [CommandMethod("GARCVERSION")]
        public void Version()
        {
            var doc = Active;
            if (doc != null) RecordVersion(doc, fromSave: false);
        }

        /// 지금 도면 상태를 버전으로 기록. 저장 직후(fromSave)에는 GA_ 레이어·선박 정보가 그대로면 묻지 않는다.
        internal static void RecordVersion(Document doc, bool fromSave)
        {
            try
            {
                RawDrawing raw;
                using (doc.LockDocument()) raw = Extractor.Extract(doc);
                var folder = VersionStore.FolderFor(doc);
                if (VersionStore.SameAsLatest(folder, raw))
                {
                    if (!fromSave) doc.Editor.WriteMessage("\n마지막 버전과 같아(GA_ 레이어·선박 정보 변화 없음) 새 버전을 만들지 않았습니다.\n");
                    return;
                }
                var idx = VersionStore.Load(folder);
                int next = (VersionStore.Latest(idx)?.Version ?? 0) + 1;
                // 마지막 검사가 지금 도면과 다르면(바뀐 뒤 아직 검사 안 함·검사 실패·감시 밖에서 바뀐 선박 정보) 다시 검사해 기록에 쓴다.
                // 같으면 다시 검사하지 않는다(같은 입력 재검사는 결과가 같고 시간만 든다).
                Watchers.TryGetValue(doc, out var w);
                if (w is { Busy: false } && (w.Dirty || w.Last == null || w.LastRaw == null
                                             || VersionStore.Fingerprint(w.LastRaw) != VersionStore.Fingerprint(raw)))
                    w.RunCheck();
                var last = w?.Last;
                // 기본값은 '직전 검사 대비'가 아니라 '마지막 기록 버전 대비'로 정한다: 엔진 비교로 해결된 위반이 있으면 '규정 위반',
                // 관련 조항은 지금 위반 중이거나 이번에 해결된 규칙의 조항. 비교를 못 하면 검사 결과의 전환으로 대신한다.
                var resolvedIds = new HashSet<string>();
                var latest = VersionStore.Latest(idx);
                bool compared = false;
                if (latest != null)
                {
                    try
                    {
                        var cmp = EngineClient.Compare(VersionStore.LoadRaw(folder, latest), raw, VersionStore.Label(latest), "현재");
                        foreach (var x in cmp.ResolvedRules) resolvedIds.Add(x.RuleId);
                        foreach (var c in cmp.Changes) foreach (var x in c.Resolved) resolvedIds.Add(x.RuleId);
                        compared = true;
                    }
                    catch { /* 엔진이 안 되거나 이전 스냅샷을 못 읽으면 아래로 */ }
                }
                if (!compared && last != null)
                    foreach (var t in last.Transitions.Where(t => t.Kind == "resolved")) resolvedIds.Add(t.RuleId);
                var refs = last?.Results.Where(r => r.Status == "fail" || resolvedIds.Contains(r.RuleId))
                               .Select(r => r.Ref).Distinct().ToList() ?? new List<string>();
                string reason = resolvedIds.Count > 0 || (last?.Results.Any(r => r.Status == "fail") ?? false) ? "rule_violation" : "other";
                string author = (AcApp.GetSystemVariable("LOGINNAME") as string) ?? Environment.UserName;
                var (fail, unknown) = w?.ElementCounts() ?? (0, 0);
                string summary = last == null ? "아직 검사 결과 없음" : $"현재 판정: {last.OverallKo} · 미충족 {fail} · 확인 필요 {unknown}";
                using var dlg = new VersionDialog(next, DocName(doc), author, reason, string.Join(" · ", refs), summary);
                if (AcApp.ShowModalDialog(dlg) != System.Windows.Forms.DialogResult.OK) return;
                var entry = VersionStore.Add(folder, DocName(doc), raw, dlg.Result, last);
                doc.Editor.WriteMessage($"\n버전 v{entry.Version} 기록 — {VersionStore.ReasonText(entry.Reason)} · {entry.Author} ({folder})\n");
                if (CompareForms.TryGetValue(doc, out var cf) && !cf.IsDisposed) cf.Close();   // 열린 비교 창은 다시 열 때 새 목록으로
            }
            catch (System.Exception ex)
            {
                doc.Editor.WriteMessage("\n버전 기록 실패: " + ex.Message + "\n");
            }
        }

        [CommandMethod("GARCCOMPARE")]
        public void Compare()
        {
            var doc = Active;
            if (doc == null) return;
            if (CompareForms.TryGetValue(doc, out var open) && !open.IsDisposed) { open.Activate(); return; }
            if (!EngineClient.EnsureRunning())
            {
                doc.Editor.WriteMessage("\n엔진 서버에 연결할 수 없어 비교할 수 없습니다. 'python -m garc serve'를 실행하세요.\n");
                return;
            }
            // 비교 창은 연 도면(doc)에 묶인다 — 다른 도면 탭이 활성일 때는 그 도면에 표시·이동하지 않는다
            var form = new CompareForm(doc, VersionStore.FolderFor(doc),
                d =>
                {
                    if (d == null) { Overlay.ClearDiff(); return true; }
                    if (Active != doc) return false;   // 비교 창이 '현재 도면 탭이 아님'을 알린다
                    Overlay.ShowDiff(doc, d);
                    return true;
                },
                (c, scale) =>
                {
                    if (Active != doc || c.Bbox == null) return;
                    using (doc.LockDocument()) Overlay.FocusBox(doc, c.Bbox, scale);
                });
            form.FormClosed += (_, _) => CompareForms.Remove(doc);
            CompareForms[doc] = form;
            AcApp.ShowModelessDialog(form);
        }

        // ── 떠 있는 로고 패널 (기능 2) ───────────────────────────────────────

        [CommandMethod("GARCPANEL")]
        public void TogglePanel() => FloatingUi.Toggle();

        [CommandMethod("GARCLOGO")]
        public void ToggleLogo() => FloatingUi.ToggleLogo();

        // ── 내부 ────────────────────────────────────────────────────────────

        internal static DocWatcher Attach(Document doc)
        {
            if (Watchers.TryGetValue(doc, out var w)) return w;
            w = new DocWatcher(doc);
            w.Checked += x =>
            {
                if (x.Last == null) return;
                if (x.Doc == Active)
                {
                    ForPanels(p => p.ShowResult(x.Last, DocName(x.Doc)));
                    var (fail, unknown) = x.ElementCounts();
                    FloatingUi.SetStatus(x.Last.Overall, fail, unknown);
                }
                StatusTray.Update(x.Last, DocName(x.Doc));
            };
            w.Failed += (_, msg) =>
            {
                ForPanels(p => p.ShowEngineDown(msg));
                if (doc == Active) FloatingUi.SetStatus("down", 0, 0);
                StatusTray.Set("down", "검토 불가 — 엔진 연결 안 됨");
                doc.Editor.WriteMessage("\nGA-RuleCheck: " + msg + "\n");
            };
            w.VersionRequested += x => RecordVersion(x.Doc, fromSave: true);
            Watchers[doc] = w;
            return w;
        }

        internal static void Detach(Document doc)
        {
            if (!Watchers.TryGetValue(doc, out var w)) return;
            w.Dispose();
            Watchers.Remove(doc);
            Overlay.Clear();
            try { EngineClient.Reset(Extractor.DocId(doc)); } catch { }
        }

        /// 도면이 닫힐 때: 감시를 끊고, 그 도면의 비교 창도 닫는다.
        internal static void Forget(Document doc)
        {
            Detach(doc);
            if (CompareForms.TryGetValue(doc, out var cf) && !cf.IsDisposed) cf.Close();
            CompareForms.Remove(doc);
        }

        internal static void DetachAll()
        {
            foreach (var doc in new List<Document>(Watchers.Keys)) Detach(doc);
        }

        /// 도면 탭을 바꾸면 그 도면의 결과·선박 정보로 팔레트를 바꾼다. 설정된 도면이면 자동 감시.
        internal static void OnActivated(Document? doc)
        {
            if (doc == null) return;
            // 로고 배지는 패널이 접혀 있어도 보이므로, 패널 표시 여부와 상관없이 그 도면의 상태로 바꾼다
            if (Watchers.TryGetValue(doc, out var w0) && w0.Last != null)
            {
                var (f0, u0) = w0.ElementCounts();
                FloatingUi.SetStatus(w0.Last.Overall, f0, u0);
            }
            else FloatingUi.SetStatus(w0 is { LastFailed: true } ? "down" : "idle", 0, 0);
            bool shown = (_palette != null && _palette.Visible) || FloatingUi.PanelOpen;
            if (!Panels().Any() || !shown)
            {
                Overlay.ClearDiff();      // 다른 도면의 비교 표시가 남지 않게
                ReapplyCompare(doc);
                return;
            }
            Overlay.Clear();
            Overlay.ClearDiff();
            var props = DrawingProps.Read(doc.Database);
            ForPanels(p => { p.Ship.Load(props); p.ClearHistory(); });
            if (Watchers.TryGetValue(doc, out var w) && w.Last != null)
            {
                ForPanels(p => p.ShowResult(w.Last, DocName(doc), record: false));
                var (fail, unknown) = w.ElementCounts();
                FloatingUi.SetStatus(w.Last.Overall, fail, unknown);
                Redraw(doc);
            }
            else if (DrawingProps.IsConfigured(doc.Database))
            {
                Attach(doc).MarkDirty();
                ForPanels(p => p.SetIdle("검사 중…", DocName(doc)));
            }
            else
            {
                ForPanels(p => p.SetIdle("설정되지 않은 도면", DocName(doc), showSetup: true));
            }
            ReapplyCompare(doc);
        }

        /// 이 도면의 비교 창이 '도면에 표시' 중이면 다시 그린다(탭을 바꾸면 임시 표시를 지우므로).
        private static void ReapplyCompare(Document doc)
        {
            if (CompareForms.TryGetValue(doc, out var cf) && !cf.IsDisposed) cf.ReapplyDrawing();
        }

        private static void Redraw(Document? doc)
        {
            if (doc == null) return;
            if (Watchers.TryGetValue(doc, out var w) && w.Last != null)
                using (doc.LockDocument()) Overlay.Show(doc, w.Last);
            else Overlay.Clear();
        }

        /// 결과 패널 하나를 만들고 명령에 연결한다(도킹 팔레트·떠 있는 패널 공용).
        internal static ResultPanel CreatePanel()
        {
            var panel = new ResultPanel();
            panel.RecheckRequested += () => { var d = Active; if (d != null) { EngineClient.EnsureRunning(); Attach(d).RunCheck(); } };
            panel.StartRequested += () => Send("_GARCSETUP _GARC ");
            panel.LinkRequested += () => Send("_GARCLINK ");
            panel.ToggleOverlayRequested += () => Send("_GARCTOGGLE ");
            panel.ReportRequested += () => Send("_GARCREPORT ");
            panel.FocusRequested += FocusTargets;
            panel.HoverRequested += (h, on) => { var d = Active; if (d != null && Overlay.Enabled) Overlay.Hover(d, h, on); };
            panel.FitRequested += rr =>
            {
                var d = Active;
                if (d != null && Watchers.TryGetValue(d, out var ww) && ww.Last?.UnitsScale is double sc) Overlay.Fit(d, rr, sc);
            };
            panel.LargeLabelsRequested += () => Send("_GARCLARGE ");
            // 점검 항목 hover → 도면에서 그 객체를 상태 색으로 강조, 클릭 → 그 객체로 확대·이동
            panel.ElementHoverRequested += (el, on) =>
            {
                var d = Active;
                if (d == null || !Overlay.Enabled) return;
                var shape = Watchers.TryGetValue(d, out var ww) ? ww.ShapeOf(el.ElementId) : null;
                Overlay.ElementHover(d, on ? shape?.pts : null, shape?.closed ?? false, el.Status, shape?.bulges);
            };
            panel.ElementFocusRequested += el => FocusTargets(new List<string> { el.ElementId });
            panel.Ship.PickRequested += what => Send($"_GARCPICK {what} ");
            panel.Ship.EyeRequested += () => Send("_GARCEYE ");
            panel.Ship.SetupRequested += () => Send("_GARCSETUP ");
            panel.Ship.ReloadRequested += () => { var d = Active; if (d != null) panel.Ship.Load(DrawingProps.Read(d.Database)); };
            panel.Ship.SaveRequested += values =>
            {
                var d = Active;
                if (d == null) return;
                using (d.LockDocument()) DrawingProps.Write(d.Database, values);
                Attach(d).MarkDirty();
                ForPanels(p => p.ShowTab("review"));
            };
            // 새로 만든 패널도 지금 도면의 마지막 결과로 채운다
            var doc = Active;
            if (doc != null)
            {
                panel.Ship.Load(DrawingProps.Read(doc.Database));
                if (Watchers.TryGetValue(doc, out var w) && w.Last != null) panel.ShowResult(w.Last, DocName(doc), record: false);
            }
            panel.SetOverlayState(Overlay.Enabled);
            panel.SetLargeLabelsState(Overlay.Scale > 1.0);
            return panel;
        }

        internal static void InitFloating()
        {
            FloatingUi.CollapseAllRequested += () => { if (_palette != null) _palette.Visible = false; };
            FloatingUi.Init(CreatePanel);
        }

        private static void EnsurePalette()
        {
            if (_palette != null)
            {
                _palette.Visible = true;
                return;
            }
            _panel = CreatePanel();

            _palette = new PaletteSet("GA RuleCheck", new Guid("3f6c2a71-5b8e-4d0a-9c1e-7a2b4d6e8f10"))
            {
                Style = PaletteSetStyles.ShowCloseButton | PaletteSetStyles.ShowAutoHideButton | PaletteSetStyles.ShowPropertiesMenu,
                MinimumSize = new System.Drawing.Size(380, 480),
            };
            _palette.Add("검토", _panel);
            _palette.Visible = true;
            _palette.Dock = DockSides.Right;
        }

        /// 상태 표시줄 아이콘 클릭: 팔레트가 있으면 보이게, 없으면 검사 시작.
        internal static void OpenFromTray()
        {
            if (_palette != null) { _palette.Visible = true; return; }
            Send("_GARC ");
        }

        /// 모달리스 팔레트에서 명령을 실행할 때는 명령 문자열로 보낸다(문서 잠금·Undo가 명령 단위로 정리됨).
        private static void Send(string cmd) => Active?.SendStringToExecute(cmd, true, false, true);

        /// 대상 객체를 선택하고 화면을 그 범위로 맞춘다(2D 평면 뷰 기준).
        private static void FocusTargets(List<string> handles)
        {
            var doc = Active;
            if (doc == null || handles.Count == 0) return;
            var db = doc.Database;
            using (doc.LockDocument())
            using (var tr = db.TransactionManager.StartOpenCloseTransaction())
            {
                var ids = new List<ObjectId>();
                Extents3d? ext = null;
                foreach (var h in handles.Select(Extractor.BaseHandle).Distinct())   // 펼친 블록 하위 객체 → 블록
                {
                    var id = Extractor.HandleToId(db, h);
                    if (id.IsNull || id.IsErased) continue;
                    if (tr.GetObject(id, OpenMode.ForRead) is not Entity ent) continue;
                    ids.Add(id);
                    try
                    {
                        var e = ent.GeometricExtents;
                        if (ext is Extents3d cur) { cur.AddExtents(e); ext = cur; } else ext = e;
                    }
                    catch { /* 범위 없는 객체 */ }
                }
                tr.Commit();
                if (ids.Count == 0) return;
                doc.Editor.SetImpliedSelection(ids.ToArray());
                if (ext is Extents3d x)
                {
                    using var view = doc.Editor.GetCurrentView();
                    view.CenterPoint = new Point2d((x.MinPoint.X + x.MaxPoint.X) / 2, (x.MinPoint.Y + x.MaxPoint.Y) / 2);
                    view.Width = Math.Max(x.MaxPoint.X - x.MinPoint.X, 1) * 3;
                    view.Height = Math.Max(x.MaxPoint.Y - x.MinPoint.Y, 1) * 3;
                    doc.Editor.SetCurrentView(view);
                }
            }
        }
    }
}
