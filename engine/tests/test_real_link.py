"""2026-10-18 실도면 연결 — 축척·원점·역할 지정·레이어 역할+측면도 영역·블록 펼침·LBP 대조·준비 상태·치수표 읽기.

실도면(조선소 GA)은 저장소에 넣지 않는다. 여기서는 실도면 5건에서 확인한 특징을 합성으로 재현한다:
  mm 시트에 1/1000로 그림, 원점이 AP·기선이 아님, GA_ 레이어 없음(0·PDF_형상 레이어), 한 시트에 측면도·평면도,
  컨테이너·크레인이 블록, 주요 치수표가 TEXT 칸 또는 여러 줄 MTEXT 두 열.
"""
import importlib.util
import json
import sys
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from garc.contract import raw_to_model  # noqa: E402
from garc.engine import evaluate  # noqa: E402
from garc.particulars import find_particulars  # noqa: E402
from garc.readiness import readiness  # noqa: E402
from garc.session import Session  # noqa: E402

VIS, BHD = "SOLAS-V-22.1.1", "SOLAS-II1-12"
HULL = [(-3, 4), (-3, 13), (0, 12), (120, 12), (120, 14.5), (137, 14.5), (134, 8), (132, 0), (0, 0)]
CONTAINER = [(100, 13.5), (100, 24.0), (112, 24.0), (112, 13.5)]


def status(rep):
    return {r.rule_id: r.status for r in rep.results}


def ship_props(ox=0.0, oz=0.0, base=True):
    p = {"GARC_LOA": "140", "GARC_L_LL": "132", "GARC_X_AP": f"{ox:g}", "GARC_X_FP": f"{ox + 132:g}",
         "GARC_CONDITIONS": "만재 출항:8.0/8.0; 밸러스트 도착:6.5/4.0"}
    if base:
        p["GARC_Z_BASE"] = f"{oz:g}"
    return p


def sheet_raw(ox=500.0, oz=300.0, layer="0", roles=True, extra=(), props=None):
    """실도면처럼: mm 시트(INSUNITS=4)에 1/1000로 그림 → 도면 1단위 = 1 m. 원점은 (ox, oz)만큼 떨어져 있다."""
    def pts(seq):
        return [[x + ox, z + oz] for x, z in seq]

    def ent(h, role, seq, typ="LWPOLYLINE", **kw):
        e = {"handle": h, "layer": layer, "type": typ, "points": pts(seq), **kw}
        if roles:
            e["role"] = role
        return e

    ents = [
        ent("H1", "hull", HULL),
        ent("E1", "eye", [(18, 28)], "POINT"),
        ent("B1", "collision_bhd", [(124.5, 0), (124.5, 14.5)], "LINE"),
        ent("U1", "bulb", [(132, 1.5), (135, 3), (132, 5)]),
        *extra,
    ]
    pr = {**ship_props(ox, oz), "GARC_SCALE": "1/1000", **(props or {})}
    return {"schema": "garc.raw/1", "insunits": 4, "props": pr, "entities": ents}


def reference_raw(extra=()):
    """같은 배를 GA_ 레이어·m 단위·원점=AP/기선으로 그린 것(기존 방식)."""
    def e(h, layer, seq, typ="LWPOLYLINE"):
        return {"handle": h, "layer": layer, "type": typ, "points": [list(p) for p in seq]}
    ents = [e("H1", "GA_HULL", HULL), e("E1", "GA_EYE", [(18, 28)], "POINT"),
            e("B1", "GA_BHD_COLL", [(124.5, 0), (124.5, 14.5)], "LINE"), e("U1", "GA_BULB", [(132, 1.5), (135, 3), (132, 5)]), *extra]
    return {"schema": "garc.raw/1", "insunits": 6, "props": ship_props(), "entities": ents}


class ScaleOriginRoles(unittest.TestCase):
    def test_sheet_scale_offset_and_roles_match_reference(self):
        box = {"handle": "C1", "layer": "0", "type": "LWPOLYLINE", "role": "장애물",
               "points": [[x + 500, z + 300] for x, z in CONTAINER], "closed": True}
        ref_box = {"handle": "C1", "layer": "GA_OBST", "type": "LWPOLYLINE", "points": [list(p) for p in CONTAINER], "closed": True}
        a = evaluate(raw_to_model(sheet_raw(extra=[box])))
        b = evaluate(raw_to_model(reference_raw(extra=[ref_box])))
        self.assertEqual(status(a), {VIS: "fail", BHD: "pass"})
        self.assertEqual(status(a), status(b))
        va, vb = (next(r for r in rep.results if r.rule_id == VIS) for rep in (a, b))
        self.assertAlmostEqual(va.actual, vb.actual, places=3)          # 사각 거리 같음(원점만 다름)
        self.assertEqual(raw_to_model(sheet_raw())["units_scale"], 1.0)

    def test_without_scale_ship_is_tiny_and_stops(self):
        raw = sheet_raw()
        raw["props"].pop("GARC_SCALE")
        rep = evaluate(raw_to_model(raw))
        self.assertNotIn("pass", status(rep).values())                # 배가 0.14 m로 읽혀 LOA와 안 맞음 → 멈춤

    def test_bad_scale_stops_everything(self):
        for bad in ("1/0", "abc", "2/1000", "1/99999"):
            rep = evaluate(raw_to_model(sheet_raw(props={"GARC_SCALE": bad})))
            self.assertEqual(rep.overall, "incomplete", bad)

    def test_default_base_with_offset_origin_is_not_pass(self):
        """원점이 기선이 아닌데 기선을 안 넣으면(기본 0) 수선이 배 아래로 가 시야가 좋게 나온다 → 검토 불가."""
        raw = sheet_raw()
        raw["props"].pop("GARC_Z_BASE")
        rep = evaluate(raw_to_model(raw))
        self.assertEqual(status(rep)[VIS], "review")
        self.assertIn("기선", next(r for r in rep.results if r.rule_id == VIS).message_ko)

    def test_leftover_zero_base_with_offset_origin_is_not_pass(self):
        """GARCSETUP이 넣은 GARC_Z_BASE=0이 남은 채 AP·FP만 찍은 경우도 같다(입력값이어도 선체보다 한참 아래면 멈춤)."""
        rep = evaluate(raw_to_model(sheet_raw(props={"GARC_Z_BASE": "0"})))
        self.assertEqual(status(rep)[VIS], "review")

    def test_default_base_is_part_of_cache_key(self):
        raw = sheet_raw(oz=0.0)
        s = Session()
        raw["props"].pop("GARC_Z_BASE")
        first = s.check(raw_to_model(raw))
        raw["props"]["GARC_Z_BASE"] = "0"
        second = s.check(raw_to_model(raw))
        self.assertIn(VIS, second["rerun_rules"])                       # 기본값 → 입력값: 같은 0이어도 다시 계산
        self.assertEqual(first["overall"], second["overall"])

    def test_unknown_role_is_not_ignored(self):
        raw = sheet_raw()
        raw["entities"][0]["role"] = "deckhouse"
        rep = evaluate(raw_to_model(raw))
        self.assertEqual(rep.overall, "incomplete")

    def test_role_overrides_layer(self):
        raw = reference_raw(extra=[{"handle": "X1", "layer": "GA_OBST", "type": "LINE", "role": "hull",
                                    "points": [[50, 14.5], [60, 14.5]]}])
        m = raw_to_model(raw)
        self.assertEqual(next(o for o in m["objects"] if o["handle"] == "X1")["role"], "hull")


class LayerRolesRegion(unittest.TestCase):
    def raw(self, region="490,290,650,340"):
        # 측면도(원점 500,300) + 같은 레이어 '컨테이너'가 평면도(아래쪽 y 200 근처)와 도면 테두리에도 있다
        prof = {"handle": "C1", "layer": "컨테이너", "type": "LWPOLYLINE", "closed": True,
                "points": [[x + 500, z + 300] for x, z in CONTAINER]}
        plan = {"handle": "C2", "layer": "컨테이너", "type": "LWPOLYLINE", "closed": True,   # 평면도(측면도 아래쪽)의 같은 레이어
                "points": [[600, 150], [612, 150], [612, 190], [600, 190]]}
        border = {"handle": "BD", "layer": "컨테이너", "type": "LWPOLYLINE", "closed": True,
                  "points": [[0, 0], [1000, 0], [1000, 700], [0, 700]]}
        props = {"GARC_LAYER_ROLES": "컨테이너=장애물"}
        if region:
            props["GARC_REGION"] = region
        return sheet_raw(extra=[prof, plan, border], props=props)

    def test_only_profile_region_objects(self):
        m = raw_to_model(self.raw())
        obst = [o["handle"] for o in m["objects"] if o["role"] == "obstruction"]
        self.assertEqual(obst, ["C1"])                                 # 평면도·테두리 제외
        self.assertEqual(status(evaluate(m))[VIS], "fail")

    def test_layer_roles_need_region(self):
        rep = evaluate(raw_to_model(self.raw(region=None)))
        self.assertEqual(status(rep)[VIS], "review")

    def test_bad_layer_roles_and_region(self):
        for props in ({"GARC_LAYER_ROLES": "컨테이너=갑판실", "GARC_REGION": "0,0,1,1"},
                      {"GARC_LAYER_ROLES": "EYE=조종위치", "GARC_REGION": "0,0,1,1"},
                      {"GARC_REGION": "1,2,3"}, {"GARC_REGION": "5,5,5,9"}):
            rep = evaluate(raw_to_model(sheet_raw(props=props)))
            self.assertEqual(rep.overall, "incomplete", props)


class BlockSuggestionText(unittest.TestCase):
    def test_block_sub_handles_are_grouped(self):
        """실도면 크레인 블록은 하위 객체가 수천 개 — 제안 문구에 handle을 다 늘어놓지 않고 블록으로 묶는다."""
        subs = [{"handle": f"K1.{i}", "block": "K1", "layer": "0", "type": "LINE", "role": "obstruction",
                 "points": [[60 + i * 0.1 + 500, 14.5 + 300], [60 + i * 0.1 + 500, 28.5 + 300]]} for i in range(40)]
        tall = {"handle": "C1", "layer": "0", "type": "LWPOLYLINE", "role": "obstruction", "closed": True,
                "points": [[x + 500, z + 300] for x, z in [(100, 13.5), (100, 27.0), (112, 27.0), (112, 13.5)]]}
        rep = evaluate(raw_to_model(sheet_raw(extra=[tall, *subs])))
        vis = next(r for r in rep.results if r.rule_id == VIS)
        self.assertEqual(vis.status, "fail")
        self.assertIn("블록 K1(하위", vis.suggestion_ko)
        self.assertNotIn("K1.17", vis.suggestion_ko)
        self.assertLess(len(vis.suggestion_ko), 400)


class BlockCompare(unittest.TestCase):
    def test_moved_block_is_one_change(self):
        """크레인 블록(하위 30개)을 옮기면 변경 30건이 아니라 '블록 … 수정 (하위 객체 30개)' 한 건."""
        from garc.compare import compare_raws

        def crane(dx):
            return [{"handle": f"K1.{i}", "block": "K1", "layer": "0", "type": "LINE", "role": "obstruction",
                     "points": [[560 + dx + i * 0.2, 314.5], [560 + dx + i * 0.2, 318.0]]} for i in range(30)]
        out = compare_raws(sheet_raw(extra=crane(0)), sheet_raw(extra=crane(5)))
        self.assertEqual(len(out["changes"]), 1)
        c = out["changes"][0]
        self.assertEqual((c["kind"], c["element_id"], len(c["members"])), ("modified", "K1", 30))
        self.assertIn("하위 객체 30개", c["desc_ko"])
        self.assertAlmostEqual(c["bbox"][0], 560.0, places=6)          # 모델 좌표 = 도면 좌표(축척 1/1000·mm → 1 m)
        self.assertAlmostEqual(c["bbox"][2], 565.0 + 29 * 0.2, places=6)
        self.assertEqual(out["indirect"], [])


class LbpCheck(unittest.TestCase):
    def test_lbp_matches_ap_fp(self):
        rep = evaluate(raw_to_model(sheet_raw(props={"GARC_LBP": "132"})))
        self.assertEqual(status(rep), {VIS: "pass", BHD: "pass"})

    def test_lbp_mismatch_stops(self):
        """AP·FP를 축척이 다른 뷰에서 찍었거나 LBP 오타 → 둘 다 검토 불가."""
        rep = evaluate(raw_to_model(sheet_raw(props={"GARC_LBP": "264"})))
        self.assertEqual(set(status(rep).values()), {"review"})


class Readiness(unittest.TestCase):
    def test_lists_every_missing_input_at_once(self):
        raw = sheet_raw()
        raw["props"] = {"GARC_SCALE": "1/1000"}
        raw["entities"] = [e for e in raw["entities"] if e["handle"] == "H1"]
        r = readiness(raw_to_model(raw))
        miss = {i["key"] for i in r["items"] if i["status"] == "missing"}
        self.assertEqual(miss, {"loa", "l_ll", "x_ap", "x_fp", "conditions", "eye", "collision_bhd"})
        self.assertEqual({i["key"] for i in r["items"] if i["status"] == "default"} >= {"z_base", "eye_height"}, True)
        self.assertFalse(r["all_ready"])
        for i in r["items"]:
            self.assertTrue(i["name_ko"])                               # 내부 이름(loa, x_ap)이 아니라 한글 이름

    def test_all_ready(self):
        r = readiness(raw_to_model(sheet_raw()))
        self.assertTrue(r["all_ready"], r)
        self.assertEqual(r["ready"], r["total"])
        units = next(i for i in r["items"] if i["key"] == "units")
        self.assertIn("1/1000", units["value_ko"])

    def test_invalid_and_problems(self):
        r = readiness(raw_to_model(sheet_raw(props={"GARC_LOA": "abc", "GARC_LBP": "999"})))
        self.assertEqual(next(i for i in r["items"] if i["key"] == "loa")["status"], "invalid")
        self.assertFalse(r["all_ready"])
        self.assertTrue(r["problems_ko"])

    def test_session_output_has_readiness(self):
        out = Session().check(raw_to_model(sheet_raw()))
        self.assertTrue(out["readiness"]["all_ready"])


@unittest.skipUnless(importlib.util.find_spec("ezdxf"), "ezdxf 없음")
class DxfBlocksArcsRoles(unittest.TestCase):
    def build(self, path, as_block=True, with_xrecord=True):
        import ezdxf
        doc = ezdxf.new("R2018")
        doc.header["$INSUNITS"] = 4
        msp = doc.modelspace()
        ox, oz = 500.0, 300.0
        hull = msp.add_lwpolyline([(x + ox, z + oz) for x, z in HULL], dxfattribs={"layer": "PDF_형상"})
        eye = msp.add_point((18 + ox, 28 + oz), dxfattribs={"layer": "0"})
        bhd = msp.add_line((124.5 + ox, oz), (124.5 + ox, 14.5 + oz), dxfattribs={"layer": "0"})
        if as_block:
            blk = doc.blocks.new("blk_a")    # 실도면처럼 뜻 없는 블록 이름
            blk.add_lwpolyline([(0, 0), (0, 10.5), (12, 10.5), (12, 0)], close=True)
            blk.add_text("CONT", dxfattribs={"height": 1})     # 블록 안 문자(형상 아님)
            inner = doc.blocks.new("lashing")
            inner.add_line((0, 0), (0, 0.5))
            blk.add_blockref("lashing", (6, 10.5))              # 중첩 블록: 꼭대기 위 0.5 m
            ref = msp.add_blockref("blk_a", (100 + ox, 13.5 + oz), dxfattribs={"layer": "컨테이너"})
            obst_handle = ref.dxf.handle
        else:
            arc = msp.add_arc((106 + ox, 18 + oz), 6.0, 0, 180, dxfattribs={"layer": "0"})   # 꼭대기 z=24
            obst_handle = arc.dxf.handle
        for k, v in {**ship_props(500, 300), "GARC_SCALE": "1/1000"}.items():
            doc.header.custom_vars.append(k, v)
        if with_xrecord:
            xr = doc.rootdict.add_xrecord("GARC_ROLES")
            tags = []
            for role, h in (("hull", hull.dxf.handle), ("eye", eye.dxf.handle), ("collision_bhd", bhd.dxf.handle),
                            ("obstruction", obst_handle)):
                tags += [(1, role), (330, h)]
            xr.reset(tags)
        doc.saveas(path)
        return obst_handle

    def test_block_obstruction_is_expanded(self):
        from garc.adapters.dxf import read_dxf
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "sheet.dxf"
            h = self.build(f)
            raw = read_dxf(f)
        subs = [e for e in raw["entities"] if e.get("block") == h]
        self.assertEqual(sorted(e["handle"] for e in subs), [f"{h}.0", f"{h}.1"])   # 사각형 + 중첩 블록 선, 문자 제외
        self.assertTrue(all(e["role"] == "obstruction" for e in subs))
        rep = evaluate(raw_to_model(raw))
        vis = next(r for r in rep.results if r.rule_id == VIS)
        self.assertEqual(vis.status, "fail")
        self.assertEqual(vis.evidence["conditions"][0]["critical_handle"].split(".")[0], h)
        top = max(p[1] for e in raw_to_model(raw)["objects"] if e["role"] == "obstruction" for p in e["points"])
        self.assertAlmostEqual(top, 324.5, places=6)                   # 컨테이너 꼭대기 24 + 중첩 블록 0.5

    def test_arc_obstruction_uses_bulge(self):
        from garc.adapters.dxf import read_dxf
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "arc.dxf"
            self.build(f, as_block=False)
            raw = read_dxf(f)
        arc = next(e for e in raw["entities"] if e["type"] == "ARC")
        self.assertEqual(len(arc["bulges"]), 2)
        m = raw_to_model(raw)
        top = max(p[1] for o in m["objects"] if o["role"] == "obstruction" for p in o["points"])
        self.assertGreaterEqual(top, 324.0 - 1e-9)                     # 외접 근사 — 호 꼭대기보다 낮게 보지 않음
        self.assertLess(top, 324.0 + 1e-3)

    def test_without_roles_real_sheet_is_not_pass(self):
        from garc.adapters.dxf import read_dxf
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "noroles.dxf"
            self.build(f, with_xrecord=False)
            rep = evaluate(raw_to_model(read_dxf(f)))
        self.assertNotIn("pass", status(rep).values())                 # 역할 지정 전: 입력 누락


@unittest.skipUnless(importlib.util.find_spec("ezdxf"), "ezdxf 없음")
class NoSilentGeometryLoss(unittest.TestCase):
    """검증(Sonnet) HIGH: 블록을 펼쳤는데 형상이 사라지거나 360° 호가 점이 되면 장애물이 없는 것처럼 '통과'로 샜다."""

    @staticmethod
    def inject(path, type_name, block=None, handle="FE01"):
        """ezdxf가 모르는 타입(DXFTagStorage로 읽힘: PLANESURFACE·AEC_* 등)을 DXF 문자열에 직접 넣는다(ezdxf로는 만들 수 없음)."""
        import re
        t = Path(path).read_text(encoding="utf-8")
        if block:
            m = re.search(r"\n  0\nBLOCK\n(?:[^\n]*\n){0,14}?  2\n" + block + r"\n", t)
            j = t.index("\n  0\nENDBLK", m.end())
        else:
            j = t.index("\n  0\nENDSEC", t.index("\n  2\nENTITIES\n"))
        t = t[:j] + f"\n  0\n{type_name}\n  5\n{handle}\n330\n1\n100\nAcDbEntity\n  8\n0" + t[j:]
        Path(path).write_text(t, encoding="utf-8")

    def run_block(self, fill, inject=None):
        import ezdxf
        from garc.adapters.dxf import read_dxf
        doc = ezdxf.new("R2018")
        doc.header["$INSUNITS"] = 4
        msp = doc.modelspace()
        ox, oz = 500.0, 300.0
        h = msp.add_lwpolyline([(x + ox, z + oz) for x, z in HULL], dxfattribs={"layer": "선체"})
        eye = msp.add_point((18 + ox, 28 + oz))
        bhd = msp.add_line((124.5 + ox, oz), (124.5 + ox, 14.5 + oz))
        blk = doc.blocks.new("box")
        obst = None
        if fill == "solid":
            blk.add_solid([(0, 0), (12, 0), (0, 10.5), (12, 10.5)])      # 꼭짓점 순서 1·2·4·3이 둘레
        elif fill == "hatch":
            hatch = blk.add_hatch()
            hatch.paths.add_polyline_path([(0, 0), (12, 0), (12, 10.5), (0, 10.5)], is_closed=True)
        elif fill == "text":
            blk.add_text("CONTAINER", dxfattribs={"height": 1})
        elif fill == "sliver":
            blk.add_line((0, 0), (1, 0))                                  # 가리지 않는 짧은 선 — 혼자면 '충족'
        elif fill.startswith("nested_tilt"):
            # 비균일 축척 블록 안에 법선이 기운 중첩 블록 → ezdxf가 중첩 블록을 재귀 없이 평탄화하는 경로
            import ezdxf.entities.factory as factory
            inner = doc.blocks.new("inner")
            inner.add_line((0, 0), (1, 0))
            attribs = {"extrusion": (1, 0, 1)}
            if fill == "nested_tilt_proxy":
                inner.add_entity(factory.create_db_entry("ACAD_PROXY_ENTITY", {"layer": "0"}, doc))
            elif fill == "nested_tilt_minsert":
                attribs.update(column_count=3, column_spacing=5.0)        # 평탄화하면 복사본 하나만 나온다
            elif fill == "nested_tilt_xref":
                doc.add_xref_def("outside.dxf", "outside")                # 형상이 다른 파일에 있는 블록
                inner.add_line((0, 1), (1, 1))                            # 선 2개 → 개수 안전망(정의 2개 = 나온 2개)으로는 못 잡는다
                blk.add_blockref("outside", (0, 0), dxfattribs={"extrusion": (1, 0, 1)})
            blk.add_blockref("inner", (0, 0), dxfattribs=attribs)
            obst = msp.add_blockref("box", (100 + ox, 13.5 + oz), dxfattribs={"xscale": 2.0})
        elif fill == "proxy":
            # 그래픽 없는 프록시는 ezdxf가 콜백 없이 건너뛴다 — 선 하나가 함께 있으면 '펼쳐진' 것처럼 보였다
            import ezdxf.entities.factory as factory
            blk.add_line((0, 0), (1, 0))
            blk.add_entity(factory.create_db_entry("ACAD_PROXY_ENTITY", {"layer": "0"}, doc))
        if fill == "arc360":
            obst = msp.add_arc((106 + ox, 18 + oz), 6.0, 0, 360)          # 꼭대기 z=24
        elif fill == "circle":
            obst = msp.add_circle((106 + ox, 18 + oz), 6.0)
        elif fill.startswith("nested_tilt"):
            pass                                                           # obst는 위에서 만듦
        elif fill == "minsert":
            blk.add_lwpolyline([(0, 0), (0, 10.5), (12, 10.5), (12, 0)], close=True)
            obst = msp.add_blockref("box", (100 + ox, 13.5 + oz))
            obst.dxf.column_count, obst.dxf.column_spacing = 2, 13
        else:
            obst = msp.add_blockref("box", (100 + ox, 13.5 + oz))
        for k, v in {**ship_props(500, 300), "GARC_SCALE": "1/1000"}.items():
            doc.header.custom_vars.append(k, v)
        xr = doc.rootdict.add_xrecord("GARC_ROLES")
        tags = []
        for role, ent in (("hull", h), ("eye", eye), ("collision_bhd", bhd), ("obstruction", obst)):
            tags += [(1, role), (330, ent.dxf.handle)]
        xr.reset(tags)
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "b.dxf"
            doc.saveas(f)
            if inject:
                self.inject(f, *inject)
            raw = read_dxf(f)
        return raw, evaluate(raw_to_model(raw))

    def test_solid_block_is_polygon(self):
        raw, rep = self.run_block("solid")
        self.assertEqual(status(rep)[VIS], "fail")
        self.assertIn("SOLID", [e["type"] for e in raw["entities"]])

    def test_hatch_block_uses_boundary(self):
        raw, rep = self.run_block("hatch")
        self.assertEqual(status(rep)[VIS], "fail")

    def test_text_only_block_stops(self):
        raw, rep = self.run_block("text")
        self.assertEqual(status(rep)[VIS], "review")                     # 형상 0개 → 삽입점만 → 검토 불가

    def test_full_circle_arc_is_kept(self):
        raw, rep = self.run_block("arc360")
        self.assertEqual(status(rep)[VIS], "fail")
        arc = next(e for e in raw["entities"] if e["type"] == "ARC")
        self.assertEqual(arc["bulges"], [1.0, 1.0])

    def test_circle_obstruction(self):
        raw, rep = self.run_block("circle")
        self.assertEqual(status(rep)[VIS], "fail")
        c = next(e for e in raw["entities"] if e["type"] == "CIRCLE")
        self.assertEqual((len(c["points"]), c["bulges"], c["closed"]), (2, [1.0, 1.0], True))

    def test_minsert_stops(self):
        raw, rep = self.run_block("minsert")
        self.assertEqual(status(rep)[VIS], "review")

    def test_proxy_without_graphics_in_block_stops(self):
        """검증(Sonnet) 3차: 그래픽 없는 프록시 + 선 하나인 블록이 '통과'로 샜다."""
        raw, rep = self.run_block("proxy")
        self.assertEqual(status(rep)[VIS], "review")
        self.assertIn("INSERT", [e["type"] for e in raw["entities"]])

    def test_unknown_type_in_block_stops(self):
        """검증(Sonnet) 4차: ezdxf가 모르는 타입(DXFTagStorage)도 블록 안에서 알리지 않고 빠졌다."""
        raw, rep = self.run_block("sliver")
        self.assertEqual(status(rep)[VIS], "pass")                         # 대조군
        for name in ("PLANESURFACE", "AEC_WALL"):
            raw, rep = self.run_block("sliver", inject=(name, "box"))
            self.assertEqual(status(rep)[VIS], "review", name)
            self.assertIn("INSERT", [e["type"] for e in raw["entities"]])

    def test_proxy_in_flattened_nested_block_stops(self):
        """검증(Sonnet) 5차: ezdxf가 중첩 블록을 평탄화하면 안쪽 정의의 프록시가 검사 없이 빠졌다 → 정의를 재귀로 훑는다."""
        raw, rep = self.run_block("nested_tilt")
        self.assertNotIn("INSERT", [e["type"] for e in raw["entities"]])   # 대조군: 펼쳐짐
        for fill in ("nested_tilt_proxy", "nested_tilt_minsert", "nested_tilt_xref"):   # 6차: 중첩 배열 블록·외부 참조도
            raw, rep = self.run_block(fill)
            self.assertEqual(status(rep)[VIS], "review", fill)
            self.assertIn("INSERT", [e["type"] for e in raw["entities"]], fill)

    def test_unknown_type_at_top_level_is_read(self):
        """모델 공간의 모르는 타입 때문에 DXF 읽기가 죽지 않는다(레이어는 원본 태그에서 읽음)."""
        raw, rep = self.run_block("sliver", inject=("AEC_WALL", None))
        self.assertEqual(status(rep)[VIS], "pass")
        self.assertGreaterEqual(raw["layer_counts"].get("0", 0), 1)

    def test_degenerate_arc_in_raw_stops(self):
        bad = {"handle": "A1", "layer": "0", "type": "ARC", "role": "obstruction",
               "points": [[600, 318], [600, 318]], "bulges": [1.6e16, 0]}
        self.assertEqual(status(evaluate(raw_to_model(sheet_raw(extra=[bad]))))[VIS], "review")


class LayerRoleSafety(unittest.TestCase):
    def test_region_that_excludes_everything_stops(self):
        """영역을 m 값으로 잘못 넣어 매핑 레이어 객체가 전부 빠지면 장애물이 없는 것처럼 통과하지 않게."""
        box = {"handle": "C1", "layer": "컨테이너", "type": "LWPOLYLINE", "closed": True,
               "points": [[x + 500, z + 300] for x, z in CONTAINER]}
        m = raw_to_model(sheet_raw(extra=[box], props={"GARC_LAYER_ROLES": "컨테이너=장애물", "GARC_REGION": "0,0,140,30"}))
        self.assertEqual(status(evaluate(m))[VIS], "review")
        self.assertEqual(next(i for i in readiness(m)["items"] if i["key"] == "obstruction")["status"], "invalid")

    def test_mapped_layer_without_geometry_stops(self):
        """라벨 문자를 클릭해 문자뿐인 레이어를 장애물로 매핑했거나 레이어 이름이 틀리면, 장애물이 없는 채 통과하지 않게."""
        label = {"handle": "T1", "layer": "라벨", "type": "MTEXT", "points": [[606, 318]]}
        for roles in ("라벨=장애물", "없는레이어=장애물"):
            m = raw_to_model(sheet_raw(extra=[label], props={"GARC_LAYER_ROLES": roles, "GARC_REGION": "490,290,650,340"}))
            self.assertEqual(status(evaluate(m))[VIS], "review", roles)

    def test_unsupported_outside_region_is_ignored(self):
        """영역 밖(평면도)에 있는 매핑 레이어의 지원 안 되는 객체 때문에 멈추지 않는다(영역 판정이 먼저)."""
        box = {"handle": "C1", "layer": "컨테이너", "type": "LWPOLYLINE", "closed": True,
               "points": [[x + 500, z + 300] for x, z in CONTAINER]}
        far = {"handle": "W1", "layer": "컨테이너", "type": "WIPEOUT", "points": [[600, 100], [610, 100], [610, 110]]}
        rep = evaluate(raw_to_model(sheet_raw(extra=[box, far], props={"GARC_LAYER_ROLES": "컨테이너=장애물",
                                                                        "GARC_REGION": "490,290,650,340"})))
        self.assertEqual(status(rep)[VIS], "fail")

    def test_unexpanded_block_outside_region_still_stops(self):
        """검증(Sonnet) 3차 중간: 못 펼친 블록(배열·OLE)은 삽입점만 오므로, 삽입점이 영역 밖이어도 형상은 영역 안일 수 있다 → 멈춘다."""
        small = {"handle": "S1", "layer": "컨테이너", "type": "LWPOLYLINE", "closed": True,
                 "points": [[630, 300], [631, 300], [631, 301], [630, 301]]}
        far_insert = {"handle": "K1", "layer": "컨테이너", "type": "INSERT", "points": [[0, 0]]}
        props = {"GARC_LAYER_ROLES": "컨테이너=장애물", "GARC_REGION": "490,290,650,340"}
        self.assertEqual(status(evaluate(raw_to_model(sheet_raw(extra=[small], props=props))))[VIS], "pass")   # 대조군
        m = raw_to_model(sheet_raw(extra=[small, far_insert], props=props))
        self.assertEqual(status(evaluate(m))[VIS], "review")
        self.assertIn("ENTITY_UNSUPPORTED", [i["code"] for i in m["issues"]])

    def test_non_finite_coordinates_are_not_outside(self):
        """좌표가 NaN·inf면 '영역 밖'으로 버리지 않고 멈춘다(JSON NaN도 json.loads가 받아들인다). 점 1개짜리 지원 타입도."""
        small = {"handle": "S1", "layer": "컨테이너", "type": "LWPOLYLINE", "closed": True,
                 "points": [[630, 300], [631, 300], [631, 301], [630, 301]]}
        props = {"GARC_LAYER_ROLES": "컨테이너=장애물", "GARC_REGION": "490,290,650,340"}
        for typ in ("WIPEOUT", "INSERT", "POINT", " insert "):
            for v in (float("nan"), float("inf"), 10 ** 400):             # 10**400: float로 못 바꾸는 정수
                bad = {"handle": "N1", "layer": "컨테이너", "type": typ, "points": [[v, v]]}
                rep = evaluate(raw_to_model(sheet_raw(extra=[small, bad], props=props)))
                self.assertEqual(status(rep)[VIS], "review", (typ, v))

    def test_text_on_mapped_layer_is_ignored(self):
        box = {"handle": "C1", "layer": "컨테이너", "type": "LWPOLYLINE", "closed": True,
               "points": [[x + 500, z + 300] for x, z in CONTAINER]}
        label = {"handle": "T1", "layer": "컨테이너", "type": "MTEXT", "points": [[606, 318]]}
        rep = evaluate(raw_to_model(sheet_raw(extra=[box, label], props={"GARC_LAYER_ROLES": "컨테이너=장애물",
                                                                          "GARC_REGION": "490,290,650,340"})))
        self.assertEqual(status(rep)[VIS], "fail")                         # 문자 때문에 '검토 불가'가 되지 않음

    def test_ambiguous_layer_roles_stop(self):
        for text in ("컨테이너=장애물; 컨테이너=선체", "GA_OBST=선체"):
            rep = evaluate(raw_to_model(sheet_raw(props={"GARC_LAYER_ROLES": text, "GARC_REGION": "490,290,650,340"})))
            self.assertEqual(rep.overall, "incomplete", text)


class ConditionWarnings(unittest.TestCase):
    def test_even_keel_only_pass_is_flagged(self):
        rep = evaluate(raw_to_model(sheet_raw(props={"GARC_CONDITIONS": "설계 흘수(등흘수):8/8"})))
        vis = next(r for r in rep.results if r.rule_id == VIS)
        self.assertEqual(vis.status, "pass")
        self.assertIn("트림", vis.message_ko)

    def test_two_bulkheads_not_ready(self):
        extra = [{"handle": "B2", "layer": "0", "type": "LINE", "role": "collision_bhd", "points": [[620, 300], [620, 314.5]]}]
        r = readiness(raw_to_model(sheet_raw(extra=extra)))
        self.assertEqual(next(i for i in r["items"] if i["key"] == "collision_bhd")["status"], "invalid")
        self.assertFalse(r["all_ready"])


class Particulars(unittest.TestCase):
    # 합성 값(실도면 값 아님). 표 모양만 실도면 5건에서 확인한 두 가지를 흉내 낸다.
    CELLS = [  # 모양 1) 칸마다 TEXT
        {"text": "PRINCIPAL DIMENSIONS", "x": 510.0, "y": 160.0, "h": 3.0},
        {"text": "LENGTH O. A", "x": 500.0, "y": 150.0, "h": 3.0}, {"text": "251.00 M", "x": 570.0, "y": 150.0, "h": 3.0},
        {"text": "LENGTH B. P", "x": 500.0, "y": 140.0, "h": 3.0}, {"text": "243.50 M", "x": 570.0, "y": 140.0, "h": 3.0},
        {"text": "BREADTH MLD", "x": 500.0, "y": 130.0, "h": 3.0}, {"text": "40.10 M", "x": 570.0, "y": 130.0, "h": 3.0},
        {"text": "DEPTH", "x": 500.0, "y": 120.0, "h": 3.0}, {"text": "22.40 M", "x": 570.0, "y": 120.0, "h": 3.0},
        {"text": "DRAUGHT MLD (DESIGN)", "x": 500.0, "y": 110.0, "h": 3.0}, {"text": "11.30 M", "x": 570.0, "y": 110.0, "h": 3.0},
        {"text": "DRAUGHT MLD (SCANTLING)", "x": 500.0, "y": 100.0, "h": 3.0}, {"text": "12.80 M", "x": 570.0, "y": 100.0, "h": 3.0},
        {"text": "1/1000", "x": 570.0, "y": 90.0, "h": 3.0},
        {"text": "B.W TANK", "x": 160.0, "y": 150.0, "h": 1.0},          # 'B' 같은 글자가 섞여도 형폭으로 읽지 않음
    ]
    COLUMNS = [  # 모양 2) 여러 줄 MTEXT 두 열 + 표제란 SCALE
        {"text": "LENGTH O. A\n\nLENGTH B. P\n\nBREADTH MLD\n\nDEPTH\n\nDRAUGHT MLD (DESIGN)\n\nDRAUGHT MLD (SCANTLING)\n\nSCALE",
         "x": 300.0, "y": 100.0, "h": 2.5},
        {"text": "188.40M\n\n180.00M\n\n32.20M\n\n18.60M\n\n10.10M\n\n11.90M\n\n1/1000", "x": 370.0, "y": 100.0, "h": 2.5},
        {"text": "SAMPLE PRODUCT TANKER", "x": 340.0, "y": 40.0, "h": 2.5},
        {"text": "MIDSHIP SECTION", "x": 40.0, "y": 270.0, "h": 3.0},
        {"text": "SCALE", "x": 320.0, "y": 12.0, "h": 1.7}, {"text": "1/1000", "x": 345.0, "y": 8.0, "h": 3.0},
    ]

    def test_cells(self):
        out = find_particulars(self.CELLS)
        v = {k: i["value"] for k, i in out["items"].items()}
        self.assertEqual((v["loa"], v["lbp"], v["breadth"], v["depth"], v["draft_design"], v["draft_scantling"], v["scale"]),
                         (251.0, 243.5, 40.1, 22.4, 11.3, 12.8, 1000.0))
        self.assertEqual(out["suggested_props"], {"GARC_LOA": "251", "GARC_LBP": "243.5", "GARC_SCALE": "1/1000"})
        self.assertNotIn("GARC_L_LL", out["suggested_props"])           # L(ICLL)은 GA에 없다 — 추측하지 않음
        self.assertEqual([c["t_aft"] for c in out["suggested_conditions"]], [11.3, 12.8])

    def test_columns(self):
        out = find_particulars(self.COLUMNS)
        v = {k: i["value"] for k, i in out["items"].items()}
        self.assertEqual((v["loa"], v["lbp"], v["draft_design"], v["scale"]), (188.4, 180.0, 10.1, 1000.0))
        self.assertEqual(v["ship_type"], "SAMPLE PRODUCT TANKER")    # MIDSHIP SECTION은 선종 아님

    def test_misaligned_column_is_not_used(self):
        """값 열에 'REV A' 같은 줄이 끼어 한 칸씩 밀리면 그 열은 쓰지 않는다(LBP에 LOA 값이 들어가는 오독 방지)."""
        cols = [dict(self.COLUMNS[0]), dict(self.COLUMNS[1])]
        cols[1]["text"] = "REV A\n\n188.40M\n\n180.00M\n\n32.20M\n\n18.60M\n\n10.10M\n\n11.90M"
        out = find_particulars(cols)
        self.assertEqual(out["items"]["lbp"]["status"], "missing")
        self.assertEqual(out["items"]["loa"]["status"], "missing")

    def test_implausible_value_is_ignored(self):
        out = find_particulars([{"text": "LENGTH O. A", "x": 0, "y": 0, "h": 2}, {"text": "1", "x": 20, "y": 0, "h": 2}])
        self.assertEqual(out["items"]["loa"]["status"], "missing")

    def test_conflict_is_not_guessed(self):
        out = find_particulars(self.CELLS + [{"text": "LENGTH O. A", "x": 10, "y": 10, "h": 2},
                                             {"text": "300.00 M", "x": 40, "y": 10, "h": 2}])
        self.assertEqual(out["items"]["loa"]["status"], "conflict")
        self.assertIsNone(out["items"]["loa"]["value"])
        self.assertNotIn("GARC_LOA", out["suggested_props"])

    def test_garbage(self):
        out = find_particulars([{"text": None, "x": "a", "y": 1}, {"x": 1}, {"text": "LENGTH O. A", "x": 1, "y": 1, "h": 1},
                                {"text": "9999 M", "x": 5, "y": 1, "h": 1}])
        self.assertEqual(out["items"]["loa"]["status"], "missing")      # 범위 밖 값(9999 m)은 버림
        json.dumps(out, allow_nan=False)

    def test_http(self):
        from http.server import ThreadingHTTPServer
        from garc.server import Handler
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        port = httpd.server_address[1]
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            def post(body):
                req = urllib.request.Request(f"http://127.0.0.1:{port}/v1/particulars", method="POST",
                                             data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
                try:
                    return 200, json.loads(urllib.request.urlopen(req).read())
                except urllib.error.HTTPError as e:
                    return e.code, json.loads(e.read())
            code, out = post({"texts": self.COLUMNS})
            self.assertEqual((code, out["items"]["loa"]["value"]), (200, 188.4))
            self.assertEqual(post({"texts": "x"})[0], 400)
        finally:
            httpd.shutdown()
            httpd.server_close()


if __name__ == "__main__":
    unittest.main()
