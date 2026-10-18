// 실도면 연결 (GARCLINK·GARCROLE·GARCROLESHOW) — GA_ 레이어가 없는 실제 GA 도면을 검사할 수 있게 만든다.
//
// 실도면 5건 분석(10-16)에서 나온 문제에 맞춘 순서:
//   1) 치수표·축척 확인 — 도면 문자에서 엔진이 찾은 후보(/v1/particulars)를 사람이 확인 → GARC_SCALE·LOA·LBP·L·흘수 조건
//   2) 측면도 영역 — 한 시트에 평면도·단면도가 같이 있어서(GARC_REGION)
//   3) AP·FP·기선 — 원점이 AP·기선이 아니어서(FP는 AP + LBP로도)
//   4) 역할 지정 — 레이어를 옮기지 않고 객체에 역할(선체·충돌격벽·장애물·구상선수)을 붙인다(RoleMap, 도면 사전)
//   5) 조종위치 — GA_EYE 점
//   그리고 첫 검사 + 준비 상태(엔진 readiness)를 명령줄·팔레트에 보여 준다.
// 판정·단위 변환은 엔진이 한다. 여기서는 입력을 받고 도면 특성·도면 사전에 기록만 한다(레이어·객체는 바꾸지 않음).
using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using Autodesk.AutoCAD.ApplicationServices;
using Autodesk.AutoCAD.DatabaseServices;
using Autodesk.AutoCAD.EditorInput;
using Autodesk.AutoCAD.Geometry;
using Autodesk.AutoCAD.Runtime;
using AcApp = Autodesk.AutoCAD.ApplicationServices.Application;

namespace GaRuleCheck
{
    public sealed partial class Commands
    {
        // 역할을 붙일 수 있는 형상 객체(문자·치수·해치 제외)
        private static readonly SelectionFilter GeometryFilter = new(new[]
        {
            new TypedValue((int)DxfCode.Start, "LINE,LWPOLYLINE,POLYLINE,ARC,CIRCLE,ELLIPSE,SPLINE,INSERT"),
        });

        private static string Inv(double v) => v.ToString("0.###", CultureInfo.InvariantCulture);

        [CommandMethod("GARCLINK")]
        public void Link()
        {
            var doc = Active;
            if (doc == null) return;
            var ed = doc.Editor;
            var db = doc.Database;
            ed.WriteMessage("\n실도면 연결: 도면의 레이어·객체는 바꾸지 않고 축척·측면도 영역·기준점·역할만 기록합니다." +
                            "\n각 단계는 Enter로 건너뛸 수 있고, Esc로 멈춰도 그때까지 입력한 것은 남습니다. 다시 실행하면 이어서 고칩니다.\n");
            bool engine = EngineClient.EnsureRunning();
            if (!engine) ed.WriteMessage("\n엔진 서버에 연결할 수 없어 도면 문자 읽기·검사를 못 합니다. 'python -m garc serve' 후 다시 실행하세요.\n");
            EnsurePalette();

            // ── 1) 주요 치수·축척·흘수 조건 ──
            ParticularsResponse? found = null;
            if (engine)
            {
                try { found = EngineClient.Particulars(Extractor.ExtractTexts(doc)); }
                catch (System.Exception ex) { ed.WriteMessage($"\n도면 문자 읽기 실패: {ex.Message} — 값을 직접 입력하세요.\n"); }
            }
            var cur = DrawingProps.Read(db);
            using (var dlg = new ParticularsDialog(found, cur, db.Insunits == UnitsValue.Undefined, DocName(doc)))
            {
                if (AcApp.ShowModalDialog(dlg) == System.Windows.Forms.DialogResult.OK)
                {
                    DrawingProps.Write(db, dlg.Result);
                    ForPanels(p => p.Ship.Load(DrawingProps.Read(db)));
                    ed.WriteMessage("\n[1/5] 주요 치수·축척·흘수 조건을 도면 특성에 기록했습니다.");
                }
                else ed.WriteMessage("\n[1/5] 주요 치수 확인을 건너뛰었습니다.");
            }

            // ── 2) 측면도 영역 ──
            var c1 = ed.GetPoint(new PromptPointOptions("\n[2/5] 측면도 영역의 한쪽 구석 (Enter: 건너뛰기): ") { AllowNone = true });
            if (c1.Status == PromptStatus.Cancel) { Finish(doc, "멈춤"); return; }
            if (c1.Status == PromptStatus.OK)
            {
                var c2 = ed.GetCorner(new PromptCornerOptions("\n반대쪽 구석: ", c1.Value));
                if (c2.Status == PromptStatus.Cancel) { Finish(doc, "멈춤"); return; }
                if (c2.Status == PromptStatus.OK)
                {
                    var ucs = ed.CurrentUserCoordinateSystem;
                    var a = c1.Value.TransformBy(ucs);
                    var b = c2.Value.TransformBy(ucs);
                    DrawingProps.Write(db, new Dictionary<string, string>
                    {
                        ["GARC_REGION"] = $"{Inv(Math.Min(a.X, b.X))},{Inv(Math.Min(a.Y, b.Y))},{Inv(Math.Max(a.X, b.X))},{Inv(Math.Max(a.Y, b.Y))}",
                    });
                    ed.WriteMessage("\n측면도 영역을 기록했습니다(레이어 단위 역할은 이 영역 안의 객체만 씁니다).");
                }
            }

            // ── 3) AP·FP·기선 — 단위·축척을 알아야 m로 바꿀 수 있다(엔진 계산) ──
            var w = Attach(doc);
            if (engine) w.RunCheck();
            double? scale = w.Last?.UnitsScale;
            if (scale is not double s || s <= 0)
                ed.WriteMessage("\n[3/5] 도면 단위·축척을 아직 몰라 AP·FP·기선 지정을 건너뜁니다 — 1단계에서 축척을 넣고 다시 실행하세요.");
            else if (!PickReferences(doc, s)) { Finish(doc, "멈춤"); return; }

            // ── 4) 역할 지정 ──
            if (!SelectRole(doc, "hull", "\n[4/5] 선체 외곽선 선택 — 측면도의 갑판선·선수재·선미·바닥선(용골) (Enter: 건너뛰기): ")) { Finish(doc, "멈춤"); return; }
            if (!SelectRole(doc, "collision_bhd", "\n충돌격벽 선 선택 (Enter: 건너뛰기): ")) { Finish(doc, "멈춤"); return; }
            if (!PickObstructions(doc)) { Finish(doc, "멈춤"); return; }
            if (!SelectRole(doc, "bulb", "\n구상선수(수선 아래 FP 앞으로 나온 부분) 윤곽 선택 — 없으면 Enter: ")) { Finish(doc, "멈춤"); return; }

            // ── 5) 조종위치 ──
            var eye = ed.GetPoint(new PromptPointOptions("\n[5/5] 조종위치 클릭 — 조타실 앞 창 쪽, 선교 갑판(바닥) 높이 (Enter: 건너뛰기): ") { AllowNone = true });
            if (eye.Status == PromptStatus.Cancel) { Finish(doc, "멈춤"); return; }
            if (eye.Status == PromptStatus.OK) PlaceEye(doc, eye.Value.TransformBy(ed.CurrentUserCoordinateSystem));

            Finish(doc, "완료");
        }

        /// 마법사 끝(또는 멈춤): 다시 검사하고 준비 상태를 명령줄에 요약.
        private static void Finish(Document doc, string how)
        {
            var ed = doc.Editor;
            var w = Attach(doc);
            w.MarkDirty();
            w.RunCheck();
            ed.WriteMessage($"\n실도면 연결 {how}. ");
            if (w.Last?.Readiness is ReadinessDto r)
            {
                ed.WriteMessage($"준비 상태 {r.Ready}/{r.Total}{(r.AllReady ? " — 판정에 필요한 입력이 모두 있습니다." : "")}");
                foreach (var it in r.Items.Where(i => i.Status is "missing" or "invalid" || (i.Required && i.Status == "default")))
                    ed.WriteMessage($"\n  {(it.Status == "default" ? "·" : it.Status == "missing" ? "✖" : "⚠")} {it.NameKo}: " +
                                    $"{it.ProblemKo ?? it.ValueKo ?? "없음"}{(it.HintKo != null ? " — " + it.HintKo : "")}");
                foreach (var p in r.ProblemsKo.Take(5)) ed.WriteMessage($"\n  ⚠ {p}");
            }
            else if (w.Last == null) ed.WriteMessage("검사하지 못했습니다(엔진 연결 확인).");
            ed.WriteMessage("\n역할 보기(GARCROLESHOW)로 판정에 쓰인 객체를 색으로 확인하세요.\n");
        }

        /// AP·FP·기선 클릭 → m 값(도면 좌표 × 엔진이 알려 준 단위 배율). false = Esc.
        private static bool PickReferences(Document doc, double scale)
        {
            var ed = doc.Editor;
            var db = doc.Database;
            var props = DrawingProps.Read(db);
            var ucs = ed.CurrentUserCoordinateSystem;
            var write = new Dictionary<string, string>();

            var ap = ed.GetPoint(new PromptPointOptions("\n[3/5] AP(선미 수선, 보통 타두재 중심) 위치 클릭 (Enter: 건너뛰기): ") { AllowNone = true });
            if (ap.Status == PromptStatus.Cancel) return false;
            double? xAp = null;
            if (ap.Status == PromptStatus.OK) { xAp = ap.Value.TransformBy(ucs).X * scale; write["GARC_X_AP"] = DrawingProps.M(xAp.Value); }
            else if (props.TryGetValue("GARC_X_AP", out var oldAp) && double.TryParse(oldAp, NumberStyles.Float, CultureInfo.InvariantCulture, out var v)) xAp = v;

            double? lbp = props.TryGetValue("GARC_LBP", out var lb) && double.TryParse(lb, NumberStyles.Float, CultureInfo.InvariantCulture, out var lv) ? lv : null;
            var fpOpt = new PromptPointOptions(xAp != null && lbp != null
                ? $"\nFP(선수 수선) 위치 클릭 또는 [Lbp: AP + LBP {Inv(lbp.Value)} m] (Enter: 건너뛰기): "
                : "\nFP(선수 수선) 위치 클릭 (Enter: 건너뛰기): ") { AllowNone = true };
            if (xAp != null && lbp != null) { fpOpt.Keywords.Add("Lbp"); fpOpt.AppendKeywordsToMessage = false; }
            var fp = ed.GetPoint(fpOpt);
            if (fp.Status == PromptStatus.Cancel) return false;
            if (fp.Status == PromptStatus.OK) write["GARC_X_FP"] = DrawingProps.M(fp.Value.TransformBy(ucs).X * scale);
            else if (fp.Status == PromptStatus.Keyword && xAp != null && lbp != null) write["GARC_X_FP"] = DrawingProps.M(xAp.Value + lbp.Value);

            var bl = ed.GetPoint(new PromptPointOptions("\n기선(용골 바닥선) 높이 클릭 (Enter: 건너뛰기): ") { AllowNone = true });
            if (bl.Status == PromptStatus.Cancel) return false;
            if (bl.Status == PromptStatus.OK) write["GARC_Z_BASE"] = DrawingProps.M(bl.Value.TransformBy(ucs).Y * scale);

            if (write.Count > 0)
            {
                DrawingProps.Write(db, write);
                ForPanels(p => { foreach (var kv in write) p.Ship.SetField(kv.Key, kv.Value); });
                ed.WriteMessage("\n" + string.Join(", ", write.Select(kv => $"{kv.Key}={kv.Value} m")));
            }
            return true;
        }

        /// 객체를 골라 역할을 붙인다(그 역할의 이전 지정은 바꾼다). false = Esc.
        private static bool SelectRole(Document doc, string role, string message)
        {
            var ed = doc.Editor;
            var before = RoleMap.Counts(doc.Database).TryGetValue(role, out var n0) ? n0 : 0;
            var res = ed.GetSelection(new PromptSelectionOptions { MessageForAdding = message, AllowDuplicates = false }, GeometryFilter);
            if (res.Status == PromptStatus.Cancel) return false;
            if (res.Status != PromptStatus.OK || res.Value.Count == 0)
            {
                if (before > 0) ed.WriteMessage($"\n{Contract.RoleKo(role)}: 그대로 둡니다(지정된 객체 {before}개).");
                return true;
            }
            int n = RoleMap.Set(doc.Database, res.Value.GetObjectIds(), role, replaceRole: true);
            ed.WriteMessage($"\n{Contract.RoleKo(role)}: 객체 {n}개 지정" + (before > 0 ? $" (이전 {before}개는 해제)" : ""));
            return true;
        }

        /// 장애물: 객체 선택 또는 레이어 단위(측면도 영역 안만). false = Esc.
        private static bool PickObstructions(Document doc)
        {
            var ed = doc.Editor;
            var kw = new PromptKeywordOptions("\n장애물(선교 앞 갑판 화물·크레인·마스트 등) 지정 — 조타실 자체(창·지붕 차양)는 넣지 마세요 [Select/Layer/Skip] <Select>: ")
            {
                AllowNone = true,
            };
            kw.Keywords.Add("Select");
            kw.Keywords.Add("Layer");
            kw.Keywords.Add("Skip");
            kw.Keywords.Default = "Select";
            var k = ed.GetKeywords(kw);
            if (k.Status == PromptStatus.Cancel) return false;
            var choice = k.Status == PromptStatus.OK ? k.StringResult : "Select";
            if (choice == "Skip") return true;
            if (choice == "Select")
                return SelectRole(doc, "obstruction", "\n장애물 객체 선택 (걸침 창 권장, Enter: 건너뛰기): ");

            var props = DrawingProps.Read(doc.Database);
            if (!props.ContainsKey("GARC_REGION"))
                ed.WriteMessage("\n주의: 측면도 영역이 없으면 레이어 단위 역할을 쓸 수 없습니다(엔진이 '검토 불가'로 멈춤) — 2단계에서 영역을 지정하세요.");
            var pick = new PromptEntityOptions("\n장애물 레이어의 객체 하나를 클릭: ") { AllowNone = true };
            var e = ed.GetEntity(pick);
            if (e.Status == PromptStatus.Cancel) return false;
            if (e.Status != PromptStatus.OK) return true;
            string layer;
            using (var tr = doc.Database.TransactionManager.StartOpenCloseTransaction())
            {
                layer = ((Entity)tr.GetObject(e.ObjectId, OpenMode.ForRead)).Layer;
                tr.Commit();
            }
            if (layer.Contains('=') || layer.Contains(';'))
            {
                ed.WriteMessage($"\n레이어 이름 '{layer}'에 '=' 또는 ';'가 있어 레이어 단위로 지정할 수 없습니다 — 객체 선택(Select)을 쓰세요.");
                return true;
            }
            var cur = props.TryGetValue("GARC_LAYER_ROLES", out var lr) ? lr : "";
            var parts = cur.Split(';').Select(x => x.Trim()).Where(x => x.Length > 0)
                           .Where(x => !x.Split('=')[0].Trim().Equals(layer.Trim(), StringComparison.OrdinalIgnoreCase)).ToList();
            parts.Add($"{layer.Trim()}=장애물");
            DrawingProps.Write(doc.Database, new Dictionary<string, string> { ["GARC_LAYER_ROLES"] = string.Join("; ", parts) });
            ed.WriteMessage($"\n레이어 '{layer}'의 객체(측면도 영역 안)를 장애물로 씁니다.");
            return true;
        }

        [CommandMethod("GARCROLE", CommandFlags.UsePickSet)]
        public void Role()
        {
            var doc = Active;
            if (doc == null) return;
            var ed = doc.Editor;
            var sel = ed.SelectImplied();
            ObjectId[] ids;
            if (sel.Status == PromptStatus.OK && sel.Value.Count > 0) ids = sel.Value.GetObjectIds();
            else
            {
                var res = ed.GetSelection(new PromptSelectionOptions { MessageForAdding = "\n역할을 붙이거나 해제할 객체 선택: " }, GeometryFilter);
                if (res.Status != PromptStatus.OK) return;
                ids = res.Value.GetObjectIds();
            }
            var kw = new PromptKeywordOptions("\n역할 [Hull=선체/Obst=장애물/Bhd=충돌격벽/Bulb=구상선수/Eye=조종위치/Clear=해제]: ")
            {
                AppendKeywordsToMessage = false,
            };
            foreach (var k in new[] { "Hull", "Obst", "Bhd", "Bulb", "Eye", "Clear" }) kw.Keywords.Add(k);
            var r = ed.GetKeywords(kw);
            if (r.Status != PromptStatus.OK) return;
            string? role = r.StringResult switch
            {
                "Hull" => "hull", "Obst" => "obstruction", "Bhd" => "collision_bhd", "Bulb" => "bulb", "Eye" => "eye", _ => null,
            };
            int n = RoleMap.Set(doc.Database, ids, role);
            ed.WriteMessage(role == null ? $"\n객체 {n}개의 역할을 해제했습니다.\n" : $"\n객체 {n}개를 {Contract.RoleKo(role)}(으)로 지정했습니다.\n");
            if (role == "eye" && ids.Length > 1) ed.WriteMessage("주의: 조종위치는 점 1개여야 합니다(엔진이 '검토 불가'로 표시).\n");
            if (Watchers.TryGetValue(doc, out var w)) w.MarkDirty();
        }

        [CommandMethod("GARCROLESHOW")]
        public void RoleShow()
        {
            var doc = Active;
            if (doc == null) return;
            var ed = doc.Editor;
            if (Overlay.RolesShown)
            {
                Overlay.ClearRoles();
                ed.WriteMessage("\n역할 보기를 껐습니다.\n");
                return;
            }
            var w = Attach(doc);
            if (w.Last == null || w.LastRaw == null || w.Dirty)
            {
                EngineClient.EnsureRunning();
                w.RunCheck();
            }
            if (w.Last == null || w.LastRaw == null)
            {
                ed.WriteMessage("\n검사 결과가 없어 역할을 보여 줄 수 없습니다(엔진 연결 확인).\n");
                return;
            }
            var counts = Overlay.ShowRoles(doc, w.LastRaw, w.Last);
            ed.WriteMessage("\n판정에 쓰인 객체: " + (counts.Count == 0 ? "없음" :
                string.Join(" · ", counts.Select(kv => $"{Contract.RoleKo(kv.Key)} {kv.Value}"))) +
                "\n색: 선체 하늘 · 장애물 주황 · 충돌격벽 빨강 · 구상선수 파랑 · 조종위치 초록. 다시 실행하면 끕니다(도면은 그대로).\n");
        }
    }
}
