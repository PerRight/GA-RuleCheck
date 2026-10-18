// 도면 → raw 스냅샷. 판단은 하지 않고 GA_ 레이어 객체의 좌표만 도면 단위 그대로 옮긴다.
// 단위 변환·검증·호(bulge) 분할은 엔진(contract.py) 한 곳에서만 한다.
// 실도면 연결(2026-10-18): 역할을 지정한 객체(RoleMap)와 GARC_LAYER_ROLES 레이어의 객체도 보낸다(측면도 영역 거르기는 엔진).
// 역할이 있는 블록(조종위치 제외)은 펼쳐서 하위 형상을 보낸다(handle '<블록>.<번호>') — DXF 어댑터와 같은 규칙.
// ARC·CIRCLE은 점 + bulge, ELLIPSE·SPLINE은 촘촘한 꺾은선.
using System;
using System.Collections;
using System.Collections.Generic;
using System.Linq;
using System.Runtime.CompilerServices;
using Autodesk.AutoCAD.ApplicationServices;
using Autodesk.AutoCAD.DatabaseServices;

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
                if (role == null && !Contract.LayerRoles.ContainsKey(layer) && !mappedLayers.Contains(layer)) continue;
                var effRole = role ?? (Contract.LayerRoles.TryGetValue(layer, out var lr) ? lr : null);
                if (ent is BlockReference br && ent is not Table && effRole != "eye")
                {
                    int counter = 0;
                    var handle = br.Handle.ToString();
                    foreach (var sub in Expand(tr, br, handle, ref counter, 0))
                    {
                        sub.Layer = layer;
                        sub.Role = role;
                        raw.Entities.Add(sub);
                    }
                    continue;
                }
                var r = ToRaw(tr, ent, layer);
                r.Role = role;
                raw.Entities.Add(r);
            }
            tr.Commit();
            return raw;
        }

        // 블록을 펼칠 때 형상이 아닌 하위 객체(문자·치수·채우기)는 뺀다
        private static bool IsAnnotation(Entity e) =>
            e is DBText or MText or AttributeDefinition or Dimension or Leader or MLeader or Hatch or Solid or Wipeout or RasterImage;

        /// 블록 펼치기(중첩 포함). Explode 결과는 도면에 없는 임시 객체라 여기서 Dispose 한다.
        /// 펼칠 수 없으면(비균일 축척 등) 삽입점만 보내 엔진이 '검토 불가'로 멈추게 한다.
        private static IEnumerable<RawEntity> Expand(Transaction tr, BlockReference br, string parent, ref int counter, int depth)
        {
            var list = new List<RawEntity>();
            var parts = new DBObjectCollection();
            try { br.Explode(parts); }
            catch
            {
                list.Add(new RawEntity { Handle = parent, Type = "INSERT", Points = { new[] { br.Position.X, br.Position.Y } } });
                return list;
            }
            foreach (DBObject o in parts)
            {
                using (o)
                {
                    if (o is not Entity e || IsAnnotation(e)) continue;
                    if (e is BlockReference inner && e is not Table && depth < 16)
                    {
                        list.AddRange(Expand(tr, inner, parent, ref counter, depth + 1));
                        continue;
                    }
                    RawEntity r;
                    try { r = ToRaw(tr, e, ""); }
                    catch
                    {
                        // 펼친 옛 폴리선(Polyline2d/3d)은 꼭짓점이 도면에 없어 ObjectId로 읽을 수 없다 → 매개변수(정수 = 꼭짓점)로
                        r = new RawEntity { Type = "POLYLINE" };
                        if (e is Curve cv)
                            try
                            {
                                for (int i = 0; i <= (int)Math.Floor(cv.EndParam + 1e-9); i++)
                                {
                                    var pt = cv.GetPointAtParameter(i);
                                    r.Points.Add(new[] { pt.X, pt.Y });
                                }
                                r.Closed = cv.Closed;
                            }
                            catch { r.Points.Clear(); }   // 좌표 없음 → 엔진이 '검토 불가'
                    }
                    r.Handle = $"{parent}.{counter++}";
                    r.Block = parent;
                    list.Add(r);
                }
            }
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
                case Ellipse:
                case Spline:
                {
                    // 곡선 → 촘촘한 꺾은선(128구간, 닫힌 곡선은 끝점 생략). 곡선 크기의 1/10000 수준
                    var cv = (Curve)ent;
                    r.Type = ent is Spline ? "SPLINE" : "ELLIPSE";
                    try
                    {
                        double t0 = cv.StartParam, t1 = cv.EndParam;
                        const int n = 128;
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
