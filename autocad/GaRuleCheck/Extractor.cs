// 도면 → raw 스냅샷. 판단은 하지 않고 GA_ 레이어 객체의 좌표만 도면 단위 그대로 옮긴다.
// 단위 변환·검증·호(bulge) 분할은 엔진(contract.py) 한 곳에서만 한다.
// 실도면 연결(2026-10-18): 역할을 지정한 객체(RoleMap)와 GARC_LAYER_ROLES 레이어의 객체도 보낸다(측면도 영역 거르기는 엔진).
// 역할이 있는 블록(조종위치 제외)은 펼쳐서 하위 형상을 보낸다(handle '<블록>.<번호>') — DXF 어댑터와 같은 규칙.
//   주석(문자·치수·지시선)만 빼고, 솔리드는 다각형, 해치는 경계 고리마다 닫힌 꺾은선, 와이프아웃·이미지는 타입 그대로(엔진 '검토 불가').
//   펼쳐서 형상이 없거나·펼칠 수 없거나·배열 블록(MINSERT)이면 삽입점만 보내 '검토 불가'(형상을 잃고 통과로 새지 않게).
// ARC·CIRCLE은 점 + bulge(360° 호는 원), ELLIPSE·SPLINE은 촘촘한 꺾은선.
using System;
using System.Collections;
using System.Collections.Generic;
using System.Linq;
using System.Runtime.CompilerServices;
using Autodesk.AutoCAD.ApplicationServices;
using Autodesk.AutoCAD.DatabaseServices;
using Autodesk.AutoCAD.Geometry;

namespace GaRuleCheck
{
    public static class Extractor
    {
        // 엔진 세션 키. FingerprintGuid만 쓰면 같은 원본에서 '다른 이름으로 저장'한 도면(샘플 S140_rev0~4 포함)이
        // 모두 같은 값이라, 동시에 열면 이력이 섞이고 한쪽을 닫을 때 /v1/reset이 다른 쪽 이력까지 지운다.
        // → 열린 문서 인스턴스마다 고유 ID를 붙인다(문서가 닫히면 표에서 자동으로 사라짐).
        private static readonly ConditionalWeakTable<Document, string> DocIds = new();

        public static string DocId(Document doc) =>
            DocIds.GetValue(doc, d => $"{d.Database.FingerprintGuid}:{Guid.NewGuid():N}");

        public static RawDrawing Extract(Document doc)
        {
            var db = doc.Database;
            var raw = new RawDrawing
            {
                Insunits = (int)db.Insunits,
                Source = new Dictionary<string, string>
                {
                    ["app"] = "AutoCAD " + Autodesk.AutoCAD.ApplicationServices.Core.Application.GetSystemVariable("ACADVER"),
                    ["file"] = System.IO.Path.GetFileName(doc.Name),
                    ["doc_id"] = DocId(doc),
                },
            };

            IDictionaryEnumerator en = db.SummaryInfo.CustomProperties;
            while (en.MoveNext())
            {
                if (en.Key is string key)
                    raw.Props[key] = en.Value?.ToString() ?? "";
            }
            var mappedLayers = Contract.LayerRoleNames(raw.Props);

            using var tr = db.TransactionManager.StartOpenCloseTransaction();
            var roles = RoleMap.Read(tr, db);
            var bt = (BlockTable)tr.GetObject(db.BlockTableId, OpenMode.ForRead);
            var ms = (BlockTableRecord)tr.GetObject(bt[BlockTableRecord.ModelSpace], OpenMode.ForRead);
            foreach (ObjectId id in ms)
            {
                if (id.IsErased) continue;
                if (tr.GetObject(id, OpenMode.ForRead, false) is not Entity ent) continue;
                raw.LayerCounts[ent.Layer] = raw.LayerCounts.TryGetValue(ent.Layer, out var n) ? n + 1 : 1;
                var layer = ent.Layer.Trim().ToUpperInvariant();
                roles.TryGetValue(id, out var role);
                bool gaLayer = Contract.LayerRoles.ContainsKey(layer);
                if (role == null && !gaLayer && !mappedLayers.Contains(layer)) continue;
                if (role == null && !gaLayer && IsAnnotation(ent)) continue;   // 레이어째 지정한 레이어의 문자·치수는 형상이 아님
                var effRole = role ?? (gaLayer ? Contract.LayerRoles[layer] : null);
                var handle = ent.Handle.ToString();
                List<RawEntity> subs;
                if (ent is BlockReference br && ent is not Table && effRole != "eye")
                {
                    int counter = 0;
                    subs = Expand(tr, br, handle, ref counter, 0);
                }
                else
                {
                    subs = ToRawList(tr, ent);
                    if (subs.Count == 1) subs[0].Handle = handle;
                    else
                        for (int i = 0; i < subs.Count; i++) { subs[i].Handle = $"{handle}.{i}"; subs[i].Block = handle; }   // 해치 고리 여러 개
                }
                foreach (var sub in subs)
                {
                    sub.Layer = layer;
                    sub.Role = role;
                    raw.Entities.Add(sub);
                }
            }
            tr.Commit();
            return raw;
        }

        // 주석(형상 아님) — 블록을 펼칠 때와 레이어째 지정한 레이어에서 뺀다. 채움(해치·솔리드)은 형상이므로 빼지 않는다.
        private static bool IsAnnotation(Entity e) =>
            e is DBText or MText or AttributeDefinition or Dimension or Leader or MLeader or FeatureControlFrame;

        private static RawEntity InsertFallback(BlockReference br, string handle) =>
            new() { Handle = handle, Type = "INSERT", Points = { new[] { br.Position.X, br.Position.Y } } };

        /// 블록 펼치기(중첩 포함). Explode 결과는 도면에 없는 임시 객체라 여기서 Dispose 한다.
        /// 펼칠 수 없거나(비균일 축척 등) 배열 블록(MINSERT)이거나 형상이 하나도 없으면 삽입점만 보내 엔진이 '검토 불가'로 멈추게 한다.
        private static List<RawEntity> Expand(Transaction tr, BlockReference br, string parent, ref int counter, int depth)
        {
            var list = new List<RawEntity>();
            if (br is MInsertBlock || depth > 16) { list.Add(InsertFallback(br, parent)); return list; }
            var parts = new DBObjectCollection();
            try
            {
                try { br.Explode(parts); }
                catch { list.Add(InsertFallback(br, parent)); return list; }
                foreach (DBObject o in parts)
                {
                    if (o is not Entity e || IsAnnotation(e)) continue;
                    if (e is ProxyEntity || e is Ole2Frame)
                    {
                        list.Clear();
                        list.Add(InsertFallback(br, parent));       // 형상을 알 수 없는 프록시·OLE → 전체를 멈춤(DXF 어댑터와 같게)
                        return list;
                    }
                    if (e is BlockReference inner && e is not Table)
                    {
                        var sub = Expand(tr, inner, parent, ref counter, depth + 1);
                        if (sub.Count == 1 && sub[0].Type == "INSERT" && sub[0].Handle == parent)
                        {
                            list.Clear();
                            list.Add(InsertFallback(br, parent));   // 안쪽 블록을 못 펼침 → 전체를 멈춤
                            return list;
                        }
                        list.AddRange(sub);
                        continue;
                    }
                    foreach (var r in ToRawList(tr, e))
                    {
                        r.Handle = $"{parent}.{counter++}";
                        r.Block = parent;
                        list.Add(r);
                    }
                }
            }
            finally
            {
                foreach (DBObject o in parts) o.Dispose();
            }
            if (list.Count == 0) list.Add(InsertFallback(br, parent));
            return list;
        }

        /// 객체 하나 → raw 객체 목록(해치는 경계 고리 수만큼, 펼친 옛 폴리선은 선·호 조각, 나머지는 하나). handle은 호출자가 붙인다.
        private static List<RawEntity> ToRawList(Transaction tr, Entity e)
        {
            if (e is Hatch h) return HatchLoops(h);
            if ((e is Polyline2d || e is Polyline3d) && e.ObjectId.IsNull)
            {
                // 펼친 옛 폴리선은 꼭짓점이 도면에 없어 ObjectId로 읽을 수 없다 → 선·호로 다시 펼친다(호를 잃지 않게)
                var list = new List<RawEntity>();
                var segs = new DBObjectCollection();
                try
                {
                    e.Explode(segs);
                    foreach (DBObject o in segs)
                        if (o is Entity se) list.AddRange(ToRawList(tr, se));
                }
                catch { list.Clear(); list.Add(new RawEntity { Type = "POLYLINE" }); }   // 좌표 없음 → 엔진 '검토 불가'
                finally { foreach (DBObject o in segs) o.Dispose(); }
                return list;
            }
            return new List<RawEntity> { ToRaw(tr, e, "") };
        }

        /// 해치 → 경계 고리마다 닫힌 꺾은선(폴리선 고리는 bulge 그대로, 곡선 고리는 촘촘히). 좌표는 해치 평면(OCS) → WCS.
        private static List<RawEntity> HatchLoops(Hatch h)
        {
            var list = new List<RawEntity>();
            try
            {
                var toWcs = Matrix3d.PlaneToWorld(h.Normal);
                double sign = h.Normal.Z < 0 ? -1.0 : 1.0;
                double[] W(double x, double y)
                {
                    var p = new Point3d(x, y, h.Elevation).TransformBy(toWcs);
                    return new[] { p.X, p.Y };
                }
                for (int i = 0; i < h.NumberOfLoops; i++)
                {
                    var loop = h.GetLoopAt(i);
                    var r = new RawEntity { Type = "HATCH", Closed = true };
                    if (loop.IsPolyline)
                    {
                        var bulges = new List<double>();
                        foreach (BulgeVertex bv in loop.Polyline)
                        {
                            r.Points.Add(W(bv.Vertex.X, bv.Vertex.Y));
                            bulges.Add(sign * bv.Bulge);
                        }
                        if (r.Points.Count > 1 && r.Points[0][0] == r.Points[^1][0] && r.Points[0][1] == r.Points[^1][1])
                        {
                            r.Points.RemoveAt(r.Points.Count - 1);
                            bulges.RemoveAt(bulges.Count - 1);
                        }
                        if (bulges.Any(b => b != 0.0)) r.Bulges = bulges;
                    }
                    else
                    {
                        foreach (Curve2d c in loop.Curves)
                        {
                            // 곡선은 256점(제어점 많은 스플라인 고리도 거칠지 않게)
                            var pts = c is LineSegment2d ls ? new[] { ls.StartPoint, ls.EndPoint } : c.GetSamplePoints(256);
                            foreach (var p in pts) r.Points.Add(W(p.X, p.Y));
                        }
                    }
                    if (r.Points.Count > 0) list.Add(r);
                }
            }
            catch { list.Clear(); }
            if (list.Count == 0) list.Add(new RawEntity { Type = "HATCH" });   // 경계를 못 읽음 → 좌표 없음 → 엔진 '검토 불가'
            return list;
        }

        /// 도면 문자(TEXT/MTEXT, 모델 공간) — 주요 치수표·축척 읽기용. 판단은 엔진(/v1/particulars).
        public static List<TextDto> ExtractTexts(Document doc)
        {
            var db = doc.Database;
            var list = new List<TextDto>();
            using var tr = db.TransactionManager.StartOpenCloseTransaction();
            var bt = (BlockTable)tr.GetObject(db.BlockTableId, OpenMode.ForRead);
            var ms = (BlockTableRecord)tr.GetObject(bt[BlockTableRecord.ModelSpace], OpenMode.ForRead);
            foreach (ObjectId id in ms)
            {
                if (id.IsErased) continue;
                switch (tr.GetObject(id, OpenMode.ForRead, false))
                {
                    case DBText t:
                        list.Add(new TextDto { Text = t.TextString, X = t.Position.X, Y = t.Position.Y, H = t.Height });
                        break;
                    case MText m:
                        list.Add(new TextDto { Text = m.Text, X = m.Location.X, Y = m.Location.Y, H = m.TextHeight });
                        break;
                }
                if (list.Count >= 50000) break;
            }
            tr.Commit();
            return list;
        }

        private static RawEntity ToRaw(Transaction tr, Entity ent, string layer)
        {
            var r = new RawEntity { Handle = ent.Handle.ToString(), Layer = layer };
            switch (ent)
            {
                case Line l:
                    r.Type = "LINE";
                    r.Points.Add(new[] { l.StartPoint.X, l.StartPoint.Y });
                    r.Points.Add(new[] { l.EndPoint.X, l.EndPoint.Y });
                    break;
                case Polyline p:   // LWPOLYLINE. 호(bulge)·닫힘을 함께 보내고 분할은 엔진이 한다
                {
                    r.Type = "LWPOLYLINE";
                    double sign = p.Normal.Z < 0 ? -1.0 : 1.0;   // 법선이 −Z면 WCS에서 회전 방향이 반대
                    var bulges = new List<double>();
                    for (int i = 0; i < p.NumberOfVertices; i++)
                    {
                        var pt = p.GetPoint3dAt(i);          // WCS
                        r.Points.Add(new[] { pt.X, pt.Y });
                        bulges.Add(sign * p.GetBulgeAt(i));
                    }
                    r.Closed = p.Closed;
                    if (bulges.Any(b => b != 0.0)) r.Bulges = bulges;
                    break;
                }
                case Polyline2d p2:
                {
                    r.Type = "POLYLINE";
                    double sign = p2.Normal.Z < 0 ? -1.0 : 1.0;
                    var bulges = new List<double>();
                    foreach (ObjectId vId in p2)
                    {
                        if (tr.GetObject(vId, OpenMode.ForRead) is not Vertex2d v) continue;
                        // 스플라인 맞춤 폴리선의 제어점은 곡선 위의 점이 아니다 → 제외 (DXF 어댑터와 같게)
                        if (v.VertexType == Vertex2dType.SplineControlVertex) continue;
                        // Vertex2d.Position은 OCS 좌표 → WCS는 VertexPosition으로 (LWPOLYLINE의 GetPoint3dAt과 같게)
                        var wp = p2.VertexPosition(v);
                        r.Points.Add(new[] { wp.X, wp.Y });
                        bulges.Add(sign * v.Bulge);
                    }
                    r.Closed = p2.Closed;
                    if (bulges.Any(b => b != 0.0)) r.Bulges = bulges;
                    break;
                }
                case Polyline3d p3:   // DXF에서는 POLYLINE(3D). 꼭짓점이 WCS, 호 없음
                    r.Type = "POLYLINE";
                    foreach (ObjectId vId in p3)
                    {
                        if (tr.GetObject(vId, OpenMode.ForRead) is not PolylineVertex3d v) continue;
                        if (v.VertexType == Vertex3dType.ControlVertex) continue;
                        r.Points.Add(new[] { v.Position.X, v.Position.Y });
                    }
                    r.Closed = p3.Closed;
                    break;
                case DBPoint dp:
                    r.Type = "POINT";
                    r.Points.Add(new[] { dp.Position.X, dp.Position.Y });
                    break;
                case Arc a:
                {
                    // 반시계(법선 방향 기준) 호 → 시작·끝 점 + bulge = tan(중심각/4). 법선이 −Z면 WCS에서 방향 반대
                    r.Type = "ARC";
                    double sweep = a.EndAngle - a.StartAngle;
                    if (Math.Abs(sweep) < 1e-12 || Math.Abs(Math.Abs(sweep) - 2 * Math.PI) < 1e-12)
                    {
                        // 시작각 = 끝각 → 360° 호 = 원(호 하나로 보내면 점으로 무너진다)
                        var q0 = a.GetPointAtParameter(a.StartParam);
                        var q1 = a.GetPointAtParameter(a.StartParam + Math.PI);
                        r.Points.Add(new[] { q0.X, q0.Y });
                        r.Points.Add(new[] { q1.X, q1.Y });
                        r.Bulges = new List<double> { 1.0, 1.0 };
                        r.Closed = true;
                        break;
                    }
                    if (sweep <= 0) sweep += 2 * Math.PI;
                    double sign = a.Normal.Z < 0 ? -1.0 : 1.0;
                    r.Points.Add(new[] { a.StartPoint.X, a.StartPoint.Y });
                    r.Points.Add(new[] { a.EndPoint.X, a.EndPoint.Y });
                    r.Bulges = new List<double> { sign * Math.Tan(sweep / 4), 0.0 };
                    break;
                }
                case Circle c:
                {
                    // 지름 양 끝 두 점 + 반원 두 개(bulge 1), 닫힘
                    r.Type = "CIRCLE";
                    var p0 = c.GetPointAtParameter(0);
                    var p1 = c.GetPointAtParameter(Math.PI);
                    r.Points.Add(new[] { p0.X, p0.Y });
                    r.Points.Add(new[] { p1.X, p1.Y });
                    r.Bulges = new List<double> { 1.0, 1.0 };
                    r.Closed = true;
                    break;
                }
                case Solid so:
                {
                    // 4점 채움: 꼭짓점 0·1·3·2가 둘레(2·3이 같으면 삼각형)
                    r.Type = "SOLID";
                    foreach (short k in new short[] { 0, 1, 3, 2 })
                    {
                        var p = so.GetPointAt(k);
                        if (r.Points.Count == 0 || r.Points[^1][0] != p.X || r.Points[^1][1] != p.Y) r.Points.Add(new[] { p.X, p.Y });
                    }
                    r.Closed = true;
                    break;
                }
                case Ellipse:
                case Spline:
                {
                    // 곡선 → 촘촘한 꺾은선(닫힌 곡선은 끝점 생략). 스플라인은 제어점 수에 비례(뾰족한 꼭대기를 놓치지 않게)
                    var cv = (Curve)ent;
                    r.Type = ent is Spline ? "SPLINE" : "ELLIPSE";
                    try
                    {
                        double t0 = cv.StartParam, t1 = cv.EndParam;
                        int n = ent is Spline sp ? Math.Clamp(32 * Math.Max(sp.NumControlPoints, 4), 128, 4096) : 256;
                        for (int i = 0; i <= n; i++)
                        {
                            if (i == n && cv.Closed) break;
                            var pt = cv.GetPointAtParameter(t0 + (t1 - t0) * i / n);
                            r.Points.Add(new[] { pt.X, pt.Y });
                        }
                        r.Closed = cv.Closed;
                    }
                    catch { r.Points.Clear(); }   // 좌표 없음 → 엔진이 '검토 불가'
                    break;
                }
                case Table:
                    // Table은 BlockReference의 하위 클래스 — 블록으로 오인하지 않게 먼저 거른다
                    r.Type = "TABLE";
                    break;
                case BlockReference br:
                    // 삽입점만 보낸다. 엔진은 GA_EYE 이외 레이어의 INSERT를 '검토 불가'로 멈춘다(형상 없음)
                    r.Type = "INSERT";
                    r.Points.Add(new[] { br.Position.X, br.Position.Y });
                    break;
                default:
                    // 지원하지 않는 객체도 보낸다 → 엔진이 해당 규칙을 '검토 불가'로 표시 (조용히 무시하지 않음).
                    // DXF 이름은 쓰지 않는다: 폴리페이스 메시 등도 DXF 이름이 "POLYLINE"이라 지원 타입으로 오인될 수 있음.
                    r.Type = ent.GetType().Name.ToUpperInvariant();
                    break;
            }
            return r;
        }

        /// 펼친 블록 하위 객체 handle('1F3.2')은 원래 블록 handle로 바꾼다.
        public static string BaseHandle(string handle)
        {
            int dot = handle.IndexOf('.');
            return dot > 0 ? handle[..dot] : handle;
        }

        public static ObjectId HandleToId(Database db, string handle)
        {
            try
            {
                var h = new Handle(Convert.ToInt64(BaseHandle(handle), 16));
                return db.GetObjectId(false, h, 0);
            }
            catch
            {
                return ObjectId.Null;
            }
        }
    }
}
