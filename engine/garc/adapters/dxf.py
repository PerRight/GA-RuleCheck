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
  안쪽 문자·치수·해치는 형상이 아니므로 뺀다. 중첩 블록도 펼친다.
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
_ANNOTATION = {"TEXT", "MTEXT", "ATTRIB", "ATTDEF", "DIMENSION", "LEADER", "MLEADER", "MULTILEADER", "HATCH", "SOLID",
               "WIPEOUT", "IMAGE", "TOLERANCE", "ARC_DIMENSION", "LARGE_RADIAL_DIMENSION"}
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
        sweep = (e.dxf.end_angle - e.dxf.start_angle) % 360.0 or 360.0
        flip = -1.0 if e.dxf.extrusion[2] < 0 else 1.0
        out["points"] = [_xy(e.start_point), _xy(e.end_point)]
        out["bulges"] = [flip * math.tan(math.radians(sweep) / 4.0), 0.0]
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


def _expand(insert, parent_handle: str, counter: list[int]) -> Iterable[dict[str, Any]]:
    """블록을 펼쳐 형상 하위 객체를 낸다(중첩 블록 포함). 하위 객체 handle = '<블록 handle>.<번호>'."""
    try:
        subs = list(insert.virtual_entities())
    except Exception:
        # 펼칠 수 없는 블록(비균일 축척 등) → 삽입점만 보내 엔진이 '검토 불가'로 멈추게 한다
        ent = _entity(insert)
        ent["handle"] = parent_handle
        yield ent
        return
    for sub in subs:
        t = sub.dxftype()
        if t in _ANNOTATION:
            continue
        if t == "INSERT":
            yield from _expand(sub, parent_handle, counter)
            continue
        ent = _entity(sub)
        ent["handle"] = f"{parent_handle}.{counter[0]}"
        ent["block"] = parent_handle
        counter[0] += 1
        yield ent


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


def read_dxf(path: str | Path, roles: dict[str, str] | None = None) -> dict[str, Any]:
    doc = _open(path)
    msp = doc.modelspace()
    props = {k: v for k, v in doc.header.custom_vars}
    role_map = {**read_roles(doc), **{str(k).upper(): v for k, v in (roles or {}).items()}}
    mapped_layers = _layer_role_names(props)
    entities = []
    layer_counts: dict[str, int] = {}
    for e in msp:
        layer = e.dxf.layer.strip().upper()
        layer_counts[e.dxf.layer] = layer_counts.get(e.dxf.layer, 0) + 1
        role = role_map.get(str(e.dxf.handle).upper())
        if role is None and layer not in LAYER_ROLES and layer not in mapped_layers:
            continue
        eff_role = role or LAYER_ROLES.get(layer)
        if e.dxftype() == "INSERT" and eff_role != "eye":   # 레이어 단위 역할에는 조종위치가 없다(엔진 규칙)
            subs = list(_expand(e, e.dxf.handle, [0]))
            for sub in subs:
                sub["layer"] = layer
                if role is not None:
                    sub["role"] = role
            entities.extend(subs)
            continue
        ent = _entity(e)
        ent["layer"] = layer
        if role is not None:
            ent["role"] = role
        entities.append(ent)

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
