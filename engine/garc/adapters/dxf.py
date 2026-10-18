"""DXF → raw. AutoCAD 없이 시험·시연하는 대체 경로 (계획서 '개발범위: DXF 대체 경로').

AutoCAD 플러그인(Extractor.cs)과 같은 raw를 만든다. 즉 DXF로 통과한 테스트는 플러그인 경로에도 유효하다.
- 좌표는 WCS로 보낸다(Extractor.cs의 GetPoint3dAt·Position과 같게). 폴리선·블록의 OCS(돌출 방향 −Z 등)는 여기서 풀어 준다.
- 폴리선 호(bulge)·닫힘(closed)은 그대로 전달하고, 호 분할은 엔진(contract.py)이 한다.
  ARC·CIRCLE도 점 + bulge로 보낸다. ELLIPSE·SPLINE은 촘촘한 꺾은선으로 바꾼다(곡선에서 1/10000 이내).
- 스플라인 맞춤 폴리선의 '제어점'은 곡선 위의 점이 아니므로 뺀다.
- 3D 메시(폴리페이스·폴리곤 메시)는 지원하지 않는 타입으로 보낸다 → 엔진이 '검토 불가'로 표시.
- 레이어 이름은 앞뒤 공백을 지우고 대문자로 맞춘다(Extractor.cs와 같게). 레이어별 객체 수(layer_counts)도 보내
  엔진이 GA_ 이름과 비슷한 레이어(GA-OBST 등)를 '검토 불가'로 멈추게 한다.

실도면 연결(2026-10-18)
- 역할 지정: 플러그인이 도면 사전(NOD)의 XRecord 'GARC_ROLES'에 (역할, 객체) 쌍으로 저장한 것을 읽어 각 객체에 role을 붙인다.
  시험용으로 바깥에서 {handle: 역할} 표를 줄 수도 있다(read_dxf(roles=...)).
- 도면 특성 GARC_LAYER_ROLES에 적힌 레이어의 객체도 보낸다(측면도 영역 거르기는 엔진이 한다).
- 역할이 있는 블록(INSERT, 조종위치 제외)은 펼쳐서 하위 객체를 보낸다: handle '<블록 handle>.<번호>', block = 블록 handle.
  안쪽 문자·치수·지시선(주석)만 뺀다. 솔리드는 다각형으로, 해치는 경계 고리마다 닫힌 꺾은선으로 보내고(채움도 시야를 막음),
  와이프아웃·이미지 등은 그 타입 그대로 보내 엔진이 '검토 불가'로 멈춘다. 중첩 블록도 펼친다.
  펼쳐서 형상이 하나도 없거나 펼칠 수 없거나 배열 블록(MINSERT)이면 삽입점만 보내 '검토 불가'(형상을 잃고 통과로 새지 않게).
- 360° 호(시작각 = 끝각)는 원으로 보낸다.
의존성: ezdxf (pip install ezdxf) — 엔진 코어는 이 모듈 없이 동작한다.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Iterable

from ..contract import LAYER_ROLES, SCHEMA_RAW

_SPLINE_FRAME_CONTROL_POINT = 16  # DXF VERTEX 플래그
ROLES_XRECORD = "GARC_ROLES"
# 블록을 펼칠 때 형상이 아닌 하위 객체(주석·채우기)는 뺀다
_ANNOTATION = {"TEXT", "MTEXT", "ATTRIB", "ATTDEF", "DIMENSION", "LEADER", "MLEADER", "MULTILEADER", "TOLERANCE",
               "ARC_DIMENSION", "LARGE_RADIAL_DIMENSION"}
CURVE_REL_TOL = 1e-4        # ELLIPSE·SPLINE 꺾은선 근사 허용 이격 = 곡선 크기 × 이 값


def _xy(v) -> list[float]:
    return [float(v[0]), float(v[1])]


def _curve_points(e) -> list[list[float]]:
    try:
        box = [_xy(p) for p in e.flattening(1e9)]   # 거친 근사로 크기만 잰다
        size = max(math.dist(box[0], p) for p in box) if len(box) > 1 else 1.0
        return [_xy(p) for p in e.flattening(max(size * CURVE_REL_TOL, 1e-9))]
    except Exception:
        return []


def _entity(e) -> dict[str, Any]:
    t = e.dxftype()
    out: dict[str, Any] = {"handle": e.dxf.handle, "type": t, "points": []}
    if t == "LINE":
        out["points"] = [_xy(e.dxf.start), _xy(e.dxf.end)]
    elif t == "LWPOLYLINE":
        out["points"] = [_xy(p) for p in e.vertices_in_wcs()]
        flip = -1.0 if e.dxf.extrusion[2] < 0 else 1.0   # OCS 법선이 −Z면 WCS에서 회전 방향이 반대
        bulges = [flip * float(p[2]) for p in e.get_points("xyb")]
        if any(b != 0.0 for b in bulges):
            out["bulges"] = bulges
        out["closed"] = bool(e.closed)
    elif t == "POLYLINE":
        if e.is_poly_face_mesh or e.is_polygon_mesh:
            out["type"] = "POLYFACEMESH" if e.is_poly_face_mesh else "POLYGONMESH"
            return out
        verts = [v for v in e.vertices if not (v.dxf.flags & _SPLINE_FRAME_CONTROL_POINT)]
        if e.is_2d_polyline:
            ocs = e.ocs()
            elev = e.dxf.elevation[2] if hasattr(e.dxf.elevation, "__getitem__") else 0.0
            out["points"] = [_xy(ocs.to_wcs((v.dxf.location[0], v.dxf.location[1], elev))) for v in verts]
            flip = -1.0 if e.dxf.extrusion[2] < 0 else 1.0
            bulges = [flip * float(v.dxf.get("bulge", 0.0)) for v in verts]
            if any(b != 0.0 for b in bulges):
                out["bulges"] = bulges
        else:  # 3D 폴리선: 꼭짓점이 WCS
            out["points"] = [_xy(v.dxf.location) for v in verts]
        out["closed"] = bool(e.is_closed)
    elif t == "ARC":
        # 반시계(OCS) 호 → 시작·끝 점 + bulge = tan(중심각/4). 법선이 −Z면 WCS에서 방향 반대
        sweep = (e.dxf.end_angle - e.dxf.start_angle) % 360.0
        if sweep < 1e-9:   # 시작각 = 끝각 → 360° 호 = 원
            ocs, c, r = e.ocs(), e.dxf.center, float(e.dxf.radius)
            out["points"] = [_xy(ocs.to_wcs((c[0] + r, c[1], c[2]))), _xy(ocs.to_wcs((c[0] - r, c[1], c[2])))]
            out["bulges"] = [1.0, 1.0]
            out["closed"] = True
            return out
        flip = -1.0 if e.dxf.extrusion[2] < 0 else 1.0
        out["points"] = [_xy(e.start_point), _xy(e.end_point)]
        out["bulges"] = [flip * math.tan(math.radians(sweep) / 4.0), 0.0]
    elif t in ("SOLID", "TRACE", "3DFACE"):
        # 4점 채움: 꼭짓점 순서 1·2·4·3이 둘레(3·4가 같으면 삼각형). 3DFACE는 1·2·3·4
        v = [e.dxf.vtx0, e.dxf.vtx1, e.dxf.vtx2, e.dxf.get("vtx3", e.dxf.vtx2)]
        if t != "3DFACE":
            ocs = e.ocs()
            v = [ocs.to_wcs(p) for p in v]
            v = [v[0], v[1], v[3], v[2]]
        pts = []
        for p in v:
            if not pts or _xy(p) != pts[-1]:
                pts.append(_xy(p))
        out["type"] = "SOLID"
        out["points"] = pts
        out["closed"] = True
    elif t == "CIRCLE":
        ocs, c, r = e.ocs(), e.dxf.center, float(e.dxf.radius)
        out["points"] = [_xy(ocs.to_wcs((c[0] + r, c[1], c[2]))), _xy(ocs.to_wcs((c[0] - r, c[1], c[2])))]
        out["bulges"] = [1.0, 1.0]
        out["closed"] = True
    elif t in ("ELLIPSE", "SPLINE"):
        out["points"] = _curve_points(e)
    elif t == "POINT":
        out["points"] = [_xy(e.dxf.location)]
    elif t == "INSERT":
        out["points"] = [_xy(e.ocs().to_wcs(e.dxf.insert))]
    return out


def _hatch_loops(e) -> list[dict[str, Any]]:
    """해치 → 경계 고리마다 닫힌 꺾은선(호·곡선은 크기의 1/10000 이내로 꺾은선)."""
    from ezdxf import path as ezpath
    loops = []
    try:
        for p in ezpath.from_hatch(e):
            box = [_xy(v) for v in p.flattening(1e9)]
            size = max((math.dist(box[0], q) for q in box), default=1.0) or 1.0
            pts = [_xy(v) for v in p.flattening(max(size * CURVE_REL_TOL, 1e-9))]
            if pts:
                loops.append({"type": "HATCH", "points": pts, "closed": True})
    except Exception:
        return []
    return loops


def _entities(e) -> list[dict[str, Any]]:
    """객체 하나 → raw 객체 목록(해치는 경계 고리 수만큼, 나머지는 하나). handle은 호출자가 붙인다."""
    if e.dxftype() == "HATCH":
        return _hatch_loops(e) or [{"type": "HATCH", "points": []}]   # 경계를 못 읽으면 좌표 없음 → 검토 불가
    ent = _entity(e)
    ent.pop("handle", None)
    return [ent]


def _insert_fallback(insert, handle: str) -> dict[str, Any]:
    ent = _entity(insert)
    ent["handle"] = handle
    return ent


# 블록 안에 있으면 형상을 알 수 없는 객체(프록시·OLE) — 그래픽이 없으면 ezdxf가 알리지 않고 건너뛴다
_OPAQUE = {"ACAD_PROXY_ENTITY", "OLE2FRAME", "OLEFRAME"}


def _has_opaque(block, doc, tag_storage, seen: set[str]) -> bool:
    """블록 정의(중첩 블록 정의까지)에 형상을 알 수 없는 객체가 있는가. 중첩 INSERT는 ezdxf가 재귀 없이
    평탄화할 때(비균일 축척 + 기울어진 법선)도 있어, 펼치기 전에 정의를 직접 훑는다."""
    if block.name in seen:
        return False
    seen.add(block.name)
    for e in block:
        if e.dxftype() in _OPAQUE or isinstance(e, tag_storage):
            return True
        if e.dxftype() == "INSERT":
            inner = doc.blocks.get(e.dxf.name) if doc is not None else None
            if inner is None or _has_opaque(inner, doc, tag_storage, seen):
                return True          # 정의가 없는 중첩 블록도 형상을 알 수 없다
    return False


def _expand(insert, parent_handle: str, counter: list[int], depth: int = 0) -> list[dict[str, Any]]:
    """블록을 펼쳐 형상 하위 객체를 낸다(중첩 블록 포함). 하위 객체 handle = '<블록 handle>.<번호>'.
    형상이 하나도 없거나·펼칠 수 없거나·배열 블록(MINSERT)이면 삽입점만 → 엔진 '검토 불가'."""
    if insert.dxf.get("column_count", 1) > 1 or insert.dxf.get("row_count", 1) > 1 or depth > 16:
        return [_insert_fallback(insert, parent_handle)]
    from ezdxf.entities import DXFTagStorage
    try:
        block = insert.block()
        # 그래픽 없는 프록시·ezdxf가 모르는 타입(DXFTagStorage: PLANESURFACE·POINTCLOUD·AEC_* 등)은
        # ezdxf가 콜백도 없이 빠뜨린다 → 블록 정의에 있으면 펼치지 않고 전체를 멈춤
        if block is None or _has_opaque(block, insert.doc, DXFTagStorage, set()):
            return [_insert_fallback(insert, parent_handle)]
        n_defined = sum(1 for e in block if e.dxftype() != "ATTDEF")   # ezdxf도 ATTDEF는 펼치지 않는다
    except Exception:
        return [_insert_fallback(insert, parent_handle)]
    skipped: list[str] = []
    try:
        # 프록시·OLE처럼 ezdxf가 펼치지 못하는 하위 객체는 조용히 빠진다 → 알려 주는 콜백으로 잡아 전체를 멈춘다
        subs = list(insert.virtual_entities(skipped_entity_callback=lambda ent, reason: skipped.append(ent.dxftype())))
    except Exception:
        return [_insert_fallback(insert, parent_handle)]
    if any(t not in _ANNOTATION for t in skipped):
        return [_insert_fallback(insert, parent_handle)]
    if len(subs) + len(skipped) < n_defined:
        # 정의된 객체보다 적게 나왔다 = 무언가 알리지 않고 빠졌다(위 목록에 없는 경우까지 막는 안전망)
        return [_insert_fallback(insert, parent_handle)]
    out: list[dict[str, Any]] = []
    for sub in subs:
        t = sub.dxftype()
        if t in _ANNOTATION:
            continue
        if t == "INSERT":
            inner = _expand(sub, parent_handle, counter, depth + 1)
            if len(inner) == 1 and inner[0]["handle"] == parent_handle and inner[0]["type"] == "INSERT":
                return inner                     # 안쪽 블록을 못 펼침 → 전체를 멈춤
            out.extend(inner)
            continue
        for ent in _entities(sub):
            ent["handle"] = f"{parent_handle}.{counter[0]}"
            ent["block"] = parent_handle
            counter[0] += 1
            out.append(ent)
    if not out:
        return [_insert_fallback(insert, parent_handle)]
    return out


def read_roles(doc) -> dict[str, str]:
    """도면 사전 XRecord 'GARC_ROLES' → {handle: 역할}. 형식: (1, 역할) 다음 (330, 객체 handle) 반복."""
    out: dict[str, str] = {}
    try:
        xr = doc.rootdict.get(ROLES_XRECORD)
    except Exception:
        return out
    if xr is None or xr.dxftype() != "XRECORD":
        return out
    role = None
    for code, value in xr.tags:
        if code == 1:
            role = str(value)
        elif code in (330, 340, 350, 360) and role:
            out[str(value).upper()] = role
    return out


def _layer_role_names(props: dict[str, str]) -> set[str]:
    """GARC_LAYER_ROLES의 레이어 이름만(어떤 역할인지·형식 검사는 엔진이 한다)."""
    text = next((v for k, v in props.items() if str(k).strip().upper() == "GARC_LAYER_ROLES"), "")
    names = set()
    for chunk in str(text or "").replace("\n", ";").split(";"):
        if "=" in chunk:
            names.add(chunk.rsplit("=", 1)[0].strip().upper())
    return names


def _open(path: str | Path):
    import ezdxf  # 선택 의존
    try:
        return ezdxf.readfile(str(path))
    except ezdxf.DXFStructureError:
        from ezdxf import recover   # 다른 프로그램이 만든 DXF(구조 오류)
        doc, _ = recover.readfile(str(path))
        return doc


def _layer_name(e) -> str:
    """객체의 레이어 이름. ezdxf가 모르는 타입(DXFTagStorage)은 e.dxf.layer가 없으므로 원본 태그(그룹 코드 8)에서 찾는다."""
    try:
        return str(e.dxf.layer)
    except Exception:
        pass
    try:
        for sub in e.xtags.subclasses:
            for tag in sub:
                if tag.code == 8:
                    return str(tag.value)
    except Exception:
        pass
    return "0"


def read_dxf(path: str | Path, roles: dict[str, str] | None = None) -> dict[str, Any]:
    doc = _open(path)
    msp = doc.modelspace()
    props = {k: v for k, v in doc.header.custom_vars}
    role_map = {**read_roles(doc), **{str(k).upper(): v for k, v in (roles or {}).items()}}
    mapped_layers = _layer_role_names(props)
    entities = []
    layer_counts: dict[str, int] = {}
    for e in msp:
        layer_raw = _layer_name(e)
        layer = layer_raw.strip().upper()
        layer_counts[layer_raw] = layer_counts.get(layer_raw, 0) + 1
        role = role_map.get(str(e.dxf.handle).upper())
        if role is None and layer not in LAYER_ROLES and layer not in mapped_layers:
            continue
        eff_role = role or LAYER_ROLES.get(layer)
        if e.dxftype() == "INSERT" and eff_role != "eye":   # 레이어 단위 역할에는 조종위치가 없다(엔진 규칙)
            subs = _expand(e, e.dxf.handle, [0])
        else:
            subs = _entities(e)
            if len(subs) == 1:
                subs[0]["handle"] = e.dxf.handle
            else:                                     # 해치 고리 여러 개 → '<해치 handle>.<번호>'
                for i, sub in enumerate(subs):
                    sub["handle"] = f"{e.dxf.handle}.{i}"
                    sub["block"] = e.dxf.handle
        for sub in subs:
            sub["layer"] = layer
            if role is not None:
                sub["role"] = role
        entities.extend(subs)

    return {
        "schema": SCHEMA_RAW,
        "source": {"app": "dxf", "file": Path(path).name, "doc_id": Path(path).stem},
        "insunits": doc.header.get("$INSUNITS", 0),
        "props": props,
        "entities": entities,
        "layer_counts": layer_counts,
    }


def read_texts(path: str | Path) -> list[dict[str, Any]]:
    """모델 공간 TEXT·MTEXT(평문) → [{text, x, y, h}] — 주요 치수표·축척 읽기용(particulars.py)."""
    doc = _open(path)
    out = []
    for e in doc.modelspace().query("TEXT MTEXT"):
        try:
            if e.dxftype() == "TEXT":
                p, text, h = e.dxf.insert, e.dxf.text, e.dxf.get("height", 1.0)
            else:
                p, text, h = e.dxf.insert, e.plain_text(), e.dxf.get("char_height", 1.0)
            out.append({"text": text, "x": float(p[0]), "y": float(p[1]), "h": float(h)})
        except Exception:
            continue
    return out
