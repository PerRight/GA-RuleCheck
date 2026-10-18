// 실도면 역할 지정 저장소 — 레이어를 옮기지 않고 '객체 ↔ 역할'을 도면 사전(NOD)의 XRecord 'GARC_ROLES'에 저장한다.
// 설계자의 레이어 체계를 건드리지 않는다. 형식: (1, 역할) 다음 (330 소프트 포인터, 객체) 반복 — DXF 어댑터(read_roles)와 같다.
// 도면 데이터 변경이므로 Undo·저장에 함께 묶인다(표시용 Transient와 다름). 판정은 엔진이 한다.
using System;
using System.Collections.Generic;
using System.Linq;
using Autodesk.AutoCAD.ApplicationServices;
using Autodesk.AutoCAD.DatabaseServices;

namespace GaRuleCheck
{
    public static class RoleMap
    {
        public const string Key = "GARC_ROLES";

        /// 지워진 객체는 빼고 읽는다. 호출자의 트랜잭션 안에서.
        public static Dictionary<ObjectId, string> Read(Transaction tr, Database db)
        {
            var map = new Dictionary<ObjectId, string>();
            var nod = (DBDictionary)tr.GetObject(db.NamedObjectsDictionaryId, OpenMode.ForRead);
            if (!nod.Contains(Key)) return map;
            if (tr.GetObject(nod.GetAt(Key), OpenMode.ForRead) is not Xrecord xr || xr.Data == null) return map;
            string? role = null;
            foreach (TypedValue tv in xr.Data)
            {
                if (tv.TypeCode == (short)DxfCode.Text) role = tv.Value as string;
                else if (tv.TypeCode == (short)DxfCode.SoftPointerId && role != null && tv.Value is ObjectId id && id.IsValid && !id.IsErased)
                    map[id] = role;
            }
            return map;
        }

        public static Dictionary<ObjectId, string> Read(Database db)
        {
            using var tr = db.TransactionManager.StartOpenCloseTransaction();
            var m = Read(tr, db);
            tr.Commit();
            return m;
        }

        /// ids에 역할을 붙인다(role == null이면 역할 해제). replaceRole이면 그 역할이 붙어 있던 다른 객체의 역할을 먼저 지운다.
        /// 호출자가 문서 잠금을 잡고 있어야 한다(명령 안이면 자동).
        public static int Set(Database db, IEnumerable<ObjectId> ids, string? role, bool replaceRole = false)
        {
            using var tr = db.TransactionManager.StartTransaction();
            var map = Read(tr, db);
            if (replaceRole && role != null)
                foreach (var k in map.Where(kv => kv.Value == role).Select(kv => kv.Key).ToList()) map.Remove(k);
            int n = 0;
            foreach (var id in ids)
            {
                if (id.IsNull || id.IsErased) continue;
                if (role == null) { if (map.Remove(id)) n++; }
                else { map[id] = role; n++; }
            }
            Write(tr, db, map);
            tr.Commit();
            return n;
        }

        public static void Clear(Database db, string? role = null)
        {
            using var tr = db.TransactionManager.StartTransaction();
            var map = Read(tr, db);
            foreach (var k in map.Where(kv => role == null || kv.Value == role).Select(kv => kv.Key).ToList()) map.Remove(k);
            Write(tr, db, map);
            tr.Commit();
        }

        private static void Write(Transaction tr, Database db, Dictionary<ObjectId, string> map)
        {
            var nod = (DBDictionary)tr.GetObject(db.NamedObjectsDictionaryId, OpenMode.ForRead);
            Xrecord xr;
            if (nod.Contains(Key))
                xr = (Xrecord)tr.GetObject(nod.GetAt(Key), OpenMode.ForWrite);
            else
            {
                nod.UpgradeOpen();
                xr = new Xrecord();
                nod.SetAt(Key, xr);
                tr.AddNewlyCreatedDBObject(xr, true);
            }
            var rb = new ResultBuffer();
            if (map.Count == 0) rb.Add(new TypedValue((int)DxfCode.Text, ""));   // 빈 결과 버퍼 대신 자리표시(읽을 때 무시)
            foreach (var g in map.GroupBy(kv => kv.Value).OrderBy(g => g.Key))
                foreach (var kv in g)
                {
                    rb.Add(new TypedValue((int)DxfCode.Text, kv.Value));
                    rb.Add(new TypedValue((int)DxfCode.SoftPointerId, kv.Key));
                }
            xr.Data = rb;
        }

        /// 역할별 개수(안내 문구용)
        public static Dictionary<string, int> Counts(Database db) =>
            Read(db).GroupBy(kv => kv.Value).ToDictionary(g => g.Key, g => g.Count());
    }
}
