"""입력 계약: CAD 원본(raw) → 엔진 모델(model) 변환과 검증.

단위·좌표·레이어 규약은 전부 여기서만 처리한다. 규칙 코드는 항상 m 단위 model만 본다.
상세 명세: docs/input-contract.md

raw (AutoCAD 플러그인 / DXF 어댑터가 만든다, 도면 단위 그대로)
{
  "schema": "garc.raw/1",
  "source": {"app": "AutoCAD 2025", "file": "S140_GA.dwg", "doc_id": "..."},
  "insunits": 4,                      # DWG/DXF $INSUNITS (4=mm, 5=cm, 6=m, 0=미지정)
  "props": {"GARC_LOA": "140", ...},  # 도면 사용자 정의 특성(DWGPROPS) — 값은 문자열, 길이는 m
  "entities": [{"handle": "2A1", "layer": "GA_HULL", "type": "LWPOLYLINE",
                "points": [[x, y], ...],
                "bulges": [b0, b1, ...],      # (선택) 꼭짓점 i→i+1 구간의 bulge. 있으면 호를 외접 꺾은선으로 근사
                "closed": false,              # (선택) 닫힌 폴리선이면 마지막→처음 구간도 포함
                "role": "hull",               # (선택) 실도면 역할 지정 — 레이어를 옮기지 않고 객체에 역할을 붙인 것(레이어보다 우선)
                "block": "1F3"}],             # (선택) 블록을 펼친 하위 객체면 원래 블록(INSERT)의 handle. handle은 "1F3.0"처럼
  "layer_counts": {"GA-OBST": 3, "0": 120}   # (선택) 모델 공간 레이어별 객체 수 — GA_ 이름과 비슷한 레이어 감지용
}

실도면 연결용 도면 특성(선택, 2026-10-18)
  GARC_SCALE        도면 축척 "1/1000" — 시트에 1/N로 그린 도면. 도면 1단위 = (단위) × N
  GARC_REGION       측면도 영역 "x0,y0,x1,y1"(도면 단위) — GARC_LAYER_ROLES로 고른 객체는 이 안의 것만
  GARC_LAYER_ROLES  레이어 단위 역할 "컨테이너=장애물; HULL=선체" — 측면도 영역과 함께만 쓸 수 있다
  GARC_LBP          수선간 길이 LBP [m] — 참고값. AP–FP 간격과 대조해 축척·기준점 실수를 잡는다

model (엔진 입력, 전부 m)
{
  "schema": "garc.model/1",
  "units_scale": 0.001,               # 도면 단위 1 = 몇 m (플러그인이 오버레이를 도면에 되돌릴 때 사용)
  "ship": {"loa", "l_ll", "x_ap", "x_fp", "z_base", "eye_height", "keel_laid", "ship_type"},
  "conditions": [{"name": "밸러스트 도착", "t_aft": 6.5, "t_fwd": 4.0}],
  "objects": [{"handle", "layer", "role", "type", "points": [[x, z], ...]}],
  "issues": [{"code", "message_ko", "roles": [...]}]   # 계약 위반 → 관련 규칙을 'review'로
}
"""
from __future__ import annotations

import math
import re
import unicodedata
from typing import Any

SCHEMA_RAW = "garc.raw/1"
SCHEMA_MODEL = "garc.model/1"

# 레이어 → 역할. 레이어 이름은 대소문자 무시.
LAYER_ROLES: dict[str, str] = {
    "GA_HULL": "hull",              # 선체 측면 윤곽(선수재·선수루 갑판·불워크 포함) — 사각 장애물 + 선수 끝(x_bow)
    "GA_EYE": "eye",                # 조종위치(conning position)의 선교 갑판 높이 점 1개
    "GA_OBST": "obstruction",       # 선교 앞 장애물(갑판 화물, 크레인, 마스트 등) 측면 윤곽
    "GA_BHD_COLL": "collision_bhd", # 충돌격벽(선수격벽) 측면 선
    "GA_BULB": "bulb",              # 수선 아래 FP 앞으로 돌출한 부분(구상선수) 윤곽
}
ROLES = tuple(LAYER_ROLES.values())
# 역할 이름: 엔진 이름 또는 한글 이름(실도면 연결 마법사·도면 특성 GARC_LAYER_ROLES)
ROLE_ALIASES: dict[str, str] = {
    **{r: r for r in ROLES},
    "선체": "hull", "선체 윤곽": "hull", "조종위치": "eye", "장애물": "obstruction",
    "충돌격벽": "collision_bhd", "선수격벽": "collision_bhd", "구상선수": "bulb",
}

# ARC·CIRCLE은 points + bulges(호)로, ELLIPSE·SPLINE은 추출기가 촘촘히 꺾은선으로 바꿔 보낸다.
# SOLID(4점 채움)는 다각형으로, HATCH는 경계 고리마다 닫힌 꺾은선으로 보낸다(채움 영역도 시야를 막으므로 버리지 않는다).
SUPPORTED_TYPES = {"LINE", "LWPOLYLINE", "POLYLINE", "POINT", "INSERT", "ARC", "CIRCLE", "ELLIPSE", "SPLINE", "SOLID", "HATCH"}
# 주석(형상 아님) — 레이어 단위 역할(GARC_LAYER_ROLES)로 들어온 것은 조용히 뺀다. DXF 이름과 AutoCAD .NET 클래스 이름 모두.
ANNOTATION_TYPES = {"TEXT", "MTEXT", "DBTEXT", "ATTRIB", "ATTDEF", "ATTRIBUTEDEFINITION", "ATTRIBUTEREFERENCE",
                    "DIMENSION", "ARC_DIMENSION", "LARGE_RADIAL_DIMENSION", "ROTATEDDIMENSION", "ALIGNEDDIMENSION",
                    "RADIALDIMENSION", "RADIALDIMENSIONLARGE", "DIAMETRICDIMENSION", "ARCDIMENSION", "ORDINATEDIMENSION",
                    "LINEANGULARDIMENSION2", "POINT3ANGULARDIMENSION", "LEADER", "MLEADER", "MULTILEADER", "TOLERANCE",
                    "FEATURECONTROLFRAME"}
SCALE_MIN, SCALE_MAX = 1, 10_000     # 도면 축척 1/N의 N 범위(오타 감지용)

# $INSUNITS → 1 도면 단위당 m
INSUNITS_SCALE = {4: 0.001, 5: 0.01, 6: 1.0}
UNIT_NAMES = {"mm": 0.001, "cm": 0.01, "m": 1.0}

# 사용자 정의 특성 키 → (ship 필드, 형)
PROP_KEYS = {
    "GARC_LOA": ("loa", float),            # 전장 LOA [m] — SOLAS V/2.4 '길이'(가시거리 규칙)
    "GARC_L_LL": ("l_ll", float),          # 만재흘수선협약 길이 L [m] — SOLAS II-1/2(충돌격벽 규칙)
    "GARC_X_AP": ("x_ap", float),          # 도면 좌표계에서 AP의 x [m]
    "GARC_X_FP": ("x_fp", float),          # 도면 좌표계에서 FP의 x [m]
    "GARC_Z_BASE": ("z_base", float),      # 기선(baseline)의 도면 y [m], 기본 0
    "GARC_EYE_HEIGHT": ("eye_height", float),  # 선교 갑판 위 눈높이 [m], 기본 1.8
    "GARC_KEEL_LAID": ("keel_laid", str),  # 용골 거치일 YYYY-MM-DD
    "GARC_SHIP_TYPE": ("ship_type", str),
    "GARC_LBP": ("lbp", float),            # 수선간 길이 LBP [m] — 참고값(AP–FP 간격 대조용). L(ICLL)과 다를 수 있다
}
DEFAULT_EYE_HEIGHT = 1.8
ROUND = 6  # m 단위 소수 6자리(1 µm)로 반올림 — 부동소수점 잡음 제거용, 설계 허용오차 아님

# ship 숫자 필드의 허용 범위. 범위 밖(NaN·무한대 포함)이면 추측하지 않고 'review'로 멈춘다.
POSITIVE_FIELDS = ("loa", "l_ll", "lbp")                # > 0
FINITE_FIELDS = ("x_ap", "x_fp", "z_base")               # 유한
NONNEGATIVE_FIELDS = ("eye_height",)                     # ≥ 0
FIELD_PROP = {field: key for key, (field, _) in PROP_KEYS.items()}
# 오타·단위 실수 감지용 넓은 범위 (규정값이 아님). 예: 눈높이 1800(mm로 입력), L 1320(자릿수 오타)
EYE_HEIGHT_MAX = 5.0          # 선교 갑판 위 눈높이 [m] 상한
L_LL_MIN_RATIO = 0.5          # L_LL ≥ 0.5·LOA — 이보다 짧으면 자릿수 오타로 본다
LENGTH_TOL_RATIO, LENGTH_TOL_MIN = 0.02, 0.5   # 길이 대조 허용차: max(2 %, 0.5 m)

# 폴리선 호(bulge) 근사: 호에 '외접'하는 꺾은선(이웃 접선의 교점)으로 바꾼다.
#  - 외접 꺾은선은 항상 호의 볼록한 바깥쪽에 있다 → 장애물 그림자를 작게 보지 않는다(안전 쪽).
#    (그림자의 극값은 볼록 껍질 위에서 생기므로 닫힌 윤곽의 오목한 호는 결과에 영향이 없고,
#     열린 선의 호도 곡선에서 ARC_TOL_M 이내로만 벗어난다.)
#  - 호에서 떨어진 거리 ≤ ARC_TOL_M. 분할 수는 반지름에 맞춰 정하고 한 구간은 최대 10°.
ARC_TOL_M = 1e-4              # 0.1 mm
ARC_MAX_STEP_DEG = 10.0
ARC_MAX_SEGMENTS = 512        # 극단 입력 방지. 상한에 걸려도 외접이라 안전 쪽은 유지(근사만 거칠어짐)
BULGE_EPS = 1e-9              # 이보다 작은 bulge는 직선으로 본다(큰 반지름 상쇄 오차 방지, 새지타 < 현 길이의 1e-9배)
# 호 분할 후 전체 좌표 수 상한. 넘으면 응답이 플러그인 제한 시간(3 s)을 넘길 수 있으므로 형식 오류로 멈춘다(검토 불가).
# 실측(2 vCPU 클라우드): 둥근 모서리 장애물 1000개 ≈ 10만 점에서 계약+판정 0.6 s, 15만 점 HTTP 왕복 ≈ 1 s.
MAX_MODEL_POINTS = 150_000

# 숫자 형식: 십진수만(공백 허용). nan·inf·'1_0'·전각 숫자·쉼표 소수점은 숫자로 보지 않는다(오타를 숫자로 읽지 않게).
_NUM_RE = re.compile(r"[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?", re.ASCII)   # ASCII: 전각 숫자 제외
# GA_ 레이어와 비슷한 이름 — 그 레이어의 객체는 읽지 않으므로 '검토 불가'로 멈춘다.
# 비교 이름 = NFKC 정규화(전각 → 반각) + 대문자 + 영숫자만. 비슷함 = 정식 이름으로 시작(GA_OBST_2),
# 정식 이름의 앞부분(GA_BHD, 5자 이상), 또는 한 글자 차이(GA_0BST, 5자 이상이고 'GA'로 시작 — A-HULL 같은 다른 체계 이름 제외).
# 그래서 걸리는 이름은 모두 정규화하면 'GA'로 시작한다(플러그인 재검사 신호와 같은 범위).
_LAYER_NORM = re.compile(r"[^A-Z0-9]")


def _norm_layer(name: str) -> str:
    return _LAYER_NORM.sub("", unicodedata.normalize("NFKC", str(name)).upper())


def _edit1(a: str, b: str) -> bool:
    """편집 거리 ≤ 1 (치환·삽입·삭제 한 번)."""
    if a == b:
        return True
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) > len(b):
        a, b = b, a
    i = 0
    while i < len(a) and a[i] == b[i]:
        i += 1
    return a[i + (len(a) == len(b)):] == b[i + 1:]


def near_miss_layer(name: str) -> str | None:
    """정식 GA_ 레이어와 '비슷하지만 같지 않은' 이름이면 그 정식 이름, 아니면 None."""
    if str(name).strip().upper() in LAYER_ROLES:
        return None
    n = _norm_layer(name)
    for known in LAYER_ROLES:
        k = _norm_layer(known)
        if n.startswith(k) or (len(n) >= 5 and (k.startswith(n) or (n.startswith("GA") and _edit1(n, k)))):
            return known
    return None


class ContractError(ValueError):
    """복구할 수 없는 형식 오류(필수 구조 자체가 깨짐). 값 누락은 오류가 아니라 규칙이 na/review로 처리."""


def _r(v: float) -> float:
    return round(float(v), ROUND)


def parse_number(text: Any) -> float:
    """도면 특성·흘수 문자열 → m 값. 십진수 형식이 아니면 ValueError."""
    s = str(text).strip()
    if not _NUM_RE.fullmatch(s):
        raise ValueError(f"숫자 형식이 아닙니다: {s!r}")
    return _r(s)


def _issue(code: str, message_ko: str, roles: list[str] | None = None) -> dict[str, Any]:
    return {"code": code, "message_ko": message_ko, "roles": roles or ["*"]}


def _add_issue(issues: list[dict[str, Any]], issue: dict[str, Any]) -> None:
    """같은 issue를 두 번 넣지 않는다 (validate_model이 여러 번 불려도 결과가 같게)."""
    key = (issue["code"], issue["message_ko"], tuple(issue["roles"]))
    if all((i["code"], i["message_ko"], tuple(i["roles"])) != key for i in issues):
        issues.append(issue)


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _arc_points(p0: tuple[float, float], p1: tuple[float, float], bulge: float, tol: float) -> list[list[float]]:
    """p0→p1 호를 대신할 외접 꺾은선의 내부 점(양 끝 제외). bulge = tan(θ/4), 양수 = 반시계(AutoCAD·DXF 규약).
    tol = 호에서 허용하는 최대 이격(도면 단위)."""
    (x0, y0), (x1, y1) = p0, p1
    dx, dy = x1 - x0, y1 - y0
    c = math.hypot(dx, dy)
    if c == 0.0:
        return []
    theta = 4.0 * math.atan(bulge)                   # 부호 있는 중심각
    r = c / (2.0 * math.sin(theta / 2.0))            # 부호 있는 반지름
    h = r * math.cos(theta / 2.0)                    # 현 중점 → 중심 거리(부호: 진행 방향 왼쪽 +)
    nx, ny = -dy / c, dx / c                          # 진행 방향 왼쪽 법선
    cx, cy = (x0 + x1) / 2.0 + nx * h, (y0 + y1) / 2.0 + ny * h
    a0 = math.atan2(y0 - cy, x0 - cx)
    rad = abs(r)
    # 외접 꺾은선의 최대 이격 = rad·(1/cos(Δ/2) − 1) ≤ tol  ⇔  Δ/2 ≤ atan(√(tol·(2·rad + tol)) / rad)
    step_max = math.radians(ARC_MAX_STEP_DEG)
    if tol > 0:
        step_max = min(step_max, 2.0 * math.atan(math.sqrt(tol * (2.0 * rad + tol)) / rad))
    n = min(ARC_MAX_SEGMENTS, max(1, math.ceil(abs(theta) / step_max)))
    step = theta / n
    big = rad / math.cos(step / 2.0)                # 접선 교점까지의 거리
    return [[cx + big * math.cos(a0 + step * (j + 0.5)), cy + big * math.sin(a0 + step * (j + 0.5))] for j in range(n)]


class DegenerateArc(ValueError):
    """시작점 = 끝점인데 bulge가 있는 호(예: 360° ARC를 호 하나로 보낸 것) — 점 하나로 무너뜨리지 않고 멈춘다."""


def _entity_points(ent: dict[str, Any], tol: float | None) -> list[list[float]]:
    """raw 좌표(도면 단위) → 숫자 점 목록. 호(bulge)는 외접 꺾은선으로 바꾸고, 닫힌 폴리선은 시작점을 끝에 붙인다.
    tol = 호 근사 허용 이격(도면 단위). None이면(단위 불명) 형식만 검사하고 분할하지 않는다."""
    try:
        pts = [[float(p[0]), float(p[1])] for p in (ent.get("points") or [])]
    except (TypeError, ValueError, IndexError):
        raise ContractError(f"좌표 형식 오류 (handle {ent.get('handle')})") from None
    closed = bool(ent.get("closed"))
    bulges = ent.get("bulges") or []
    if len(pts) < 2 or tol is None:
        if bulges:
            try:
                ok = len(bulges) == len(pts) and all(math.isfinite(float(b)) for b in bulges)
            except (TypeError, ValueError):
                ok = False
            if not ok:
                raise ContractError(f"bulges 형식 오류 (handle {ent.get('handle')})")
        return pts
    if bulges and len(bulges) != len(pts):
        raise ContractError(f"bulges 개수({len(bulges)})가 꼭짓점 수({len(pts)})와 다릅니다 (handle {ent.get('handle')})")
    try:
        bulges = [float(b) for b in bulges]
    except (TypeError, ValueError):
        raise ContractError(f"bulge 값 형식 오류 (handle {ent.get('handle')})") from None
    if not all(math.isfinite(b) for b in bulges):
        raise ContractError(f"bulge 값이 유한하지 않습니다 (handle {ent.get('handle')})")
    n = len(pts)
    segs = n if closed else n - 1
    out: list[list[float]] = []
    for i in range(n):
        out.append(pts[i])
        if i < segs and bulges and abs(bulges[i]) > BULGE_EPS:
            if pts[i] == pts[(i + 1) % n]:
                raise DegenerateArc(f"handle {ent.get('handle')}: 시작점과 끝점이 같은 호(360°?)는 형상을 알 수 없습니다")
            out.extend(_arc_points(tuple(pts[i]), tuple(pts[(i + 1) % n]), bulges[i], tol))
    if closed and pts[0] != pts[-1]:
        out.append(list(pts[0]))
    return out


def parse_scale(text: Any) -> float:
    """도면 축척 '1/1000'·'1:1000'·'1000' → N(1000). 형식이 아니거나 범위 밖이면 ValueError."""
    s = str(text).strip().replace(" ", "")
    m = re.fullmatch(r"(?:1[/:])?(\d+(?:\.\d+)?)", s, re.ASCII)
    if not m:
        raise ValueError(f"축척 형식이 아닙니다: {text!r} (예: 1/1000)")
    n = float(m.group(1))
    if not (SCALE_MIN <= n <= SCALE_MAX):
        raise ValueError(f"축척 1/{n:g}는 범위({SCALE_MIN}~{SCALE_MAX}) 밖입니다")
    return n


def parse_region(text: Any) -> tuple[float, float, float, float]:
    """'x0,y0,x1,y1'(도면 단위) → (xmin, ymin, xmax, ymax). 넓이가 0이면 ValueError."""
    parts = [p for p in re.split(r"[,\s]+", str(text).strip()) if p]
    if len(parts) != 4:
        raise ValueError(f"영역은 숫자 4개(x0,y0,x1,y1)여야 합니다: {text!r}")
    x0, y0, x1, y1 = (parse_number(p) for p in parts)
    box = (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
    if not all(math.isfinite(v) for v in box) or box[2] - box[0] <= 0 or box[3] - box[1] <= 0:
        raise ValueError(f"영역의 너비·높이가 0입니다: {text!r}")
    return box


def parse_layer_roles(text: Any) -> dict[str, str]:
    """'컨테이너=장애물; HULL=hull' → {레이어(대문자·앞뒤 공백 제거): 역할}.
    모르는 역할, 같은 레이어에 다른 역할 두 번, GA_ 레이어(이미 역할이 있음)는 ValueError — 어느 쪽인지 고르지 않는다."""
    out: dict[str, str] = {}
    for chunk in str(text or "").replace("\n", ";").split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "=" not in chunk:
            raise ValueError(f"레이어 역할 형식 오류: '{chunk}' (예: 컨테이너=장애물)")
        layer, role = (v.strip() for v in chunk.rsplit("=", 1))
        r = ROLE_ALIASES.get(role.lower())
        if not layer or r is None:
            raise ValueError(f"레이어 역할 '{chunk}': 역할은 선체·장애물·조종위치·충돌격벽·구상선수 중 하나")
        if r == "eye":
            raise ValueError(f"레이어 역할 '{chunk}': 조종위치는 점 1개라 레이어 단위로 지정할 수 없습니다")
        key = layer.upper()
        if key in LAYER_ROLES:
            raise ValueError(f"레이어 역할 '{chunk}': {key}는 이미 정해진 역할({LAYER_ROLES[key]})이 있는 GA_ 레이어입니다")
        if out.get(key, r) != r:
            raise ValueError(f"레이어 '{layer}'에 역할이 두 가지({out[key]}, {r}) 지정됐습니다")
        out[key] = r
    return out


def _segment_hits_box(a: list[float], b: list[float], box: tuple[float, float, float, float]) -> bool:
    """선분 a–b가 사각형과 만나는지(Liang–Barsky)."""
    x0, y0, x1, y1 = box
    dx, dy = b[0] - a[0], b[1] - a[1]
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, a[0] - x0), (dx, x1 - a[0]), (-dy, a[1] - y0), (dy, y1 - a[1])):
        if p == 0:
            if q < 0:
                return False
        else:
            t = q / p
            if p < 0:
                t0 = max(t0, t)
            else:
                t1 = min(t1, t)
            if t0 > t1:
                return False
    return True


def _has_non_finite(pts: Any) -> bool:
    """좌표에 NaN·무한대가 있는가(형식이 틀린 좌표는 여기서 판단하지 않고 뒤의 검사에 맡긴다)."""
    try:
        return any(not math.isfinite(float(v)) for p in (pts or []) for v in list(p)[:2])
    except (TypeError, ValueError):
        return False


def _in_region(pts: list[list[float]], box: tuple[float, float, float, float]) -> bool:
    """점이나 선분이 하나라도 영역 안에 걸치면 포함 — 경계에 걸친 객체를 버리지 않는다(장애물을 덜 보지 않게).
    영역을 통째로 둘러싸기만 하는 객체(도면 테두리 등)는 넣지 않는다."""
    if not pts:
        return True
    x0, y0, x1, y1 = box
    if any(x0 <= p[0] <= x1 and y0 <= p[1] <= y1 for p in pts):
        return True
    return any(_segment_hits_box(p, q, box) for p, q in zip(pts, pts[1:]))


def parse_conditions(text: str) -> list[dict[str, Any]]:
    """'만재 출항:8.0/8.0; 밸러스트 도착:6.5/4.0' → [{name, t_aft, t_fwd}] (흘수는 AP/FP 위치, 기선 기준 m)."""
    out = []
    for chunk in (text or "").replace("\n", ";").split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        if ":" not in chunk or "/" not in chunk:
            raise ValueError(f"흘수 조건 형식 오류: '{chunk}' (예: 밸러스트 도착:6.5/4.0)")
        name, drafts = chunk.rsplit(":", 1)
        aft, fwd = drafts.split("/", 1)
        try:
            out.append({"name": name.strip(), "t_aft": parse_number(aft), "t_fwd": parse_number(fwd)})
        except ValueError:
            raise ValueError(f"흘수 조건 '{name.strip()}'의 선미/선수 흘수가 비어 있거나 숫자가 아닙니다.") from None
    return out


def raw_to_model(raw: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict) or not isinstance(raw.get("entities", []), list):
        raise ContractError("raw는 entities 리스트를 가진 객체여야 합니다.")
    issues: list[dict[str, Any]] = []
    props: dict[str, str] = {}
    dup: set[str] = set()
    for k, v in (raw.get("props") or {}).items():
        if v is None:
            continue
        key = str(k).strip().upper()
        if key in props and props[key] != str(v).strip() and key.startswith("GARC_"):
            dup.add(key)
        props[key] = str(v).strip()
    for key in sorted(dup):
        # 대소문자만 다른 같은 키가 서로 다른 값 → 어느 쪽인지 고르지 않는다
        issues.append(_issue("PROP_DUPLICATE", f"도면 특성 {key}가 대소문자만 다르게 두 번 있고 값이 다릅니다 — 하나만 남기세요."))

    # 1) 단위 — 모르면 추측하지 않고 전체를 '검토 불가'로
    scale = None
    if props.get("GARC_UNITS"):
        scale = UNIT_NAMES.get(props["GARC_UNITS"].lower())
        if scale is None:
            issues.append(_issue("UNIT_UNKNOWN", f"GARC_UNITS='{props['GARC_UNITS']}'는 지원하지 않는 단위입니다 (mm/cm/m)."))
    else:
        scale = INSUNITS_SCALE.get(raw.get("insunits"))
        if scale is None:
            issues.append(_issue("UNIT_UNKNOWN",
                                 f"도면 단위($INSUNITS={raw.get('insunits')})를 알 수 없습니다. mm 또는 m로 지정하거나 GARC_UNITS 특성을 넣으세요."))
    # 1-1) 축척 — 시트에 1/N로 그린 도면(실도면 대부분: mm 시트에 1/1000)
    scale_n = None
    if props.get("GARC_SCALE"):
        try:
            scale_n = parse_scale(props["GARC_SCALE"])
        except ValueError as e:
            issues.append(_issue("UNIT_UNKNOWN", f"GARC_SCALE='{props['GARC_SCALE']}'를 축척으로 읽을 수 없습니다 — {e}"))
            scale = None
        else:
            if scale is not None:
                scale *= scale_n

    # 1-2) 측면도 영역·레이어 단위 역할(실도면 연결)
    region = None
    if props.get("GARC_REGION"):
        try:
            region = parse_region(props["GARC_REGION"])
        except ValueError as e:
            issues.append(_issue("REGION_INVALID", f"GARC_REGION(측면도 영역)을 읽을 수 없습니다 — {e}"))
    layer_roles: dict[str, str] = {}
    if props.get("GARC_LAYER_ROLES"):
        try:
            layer_roles = parse_layer_roles(props["GARC_LAYER_ROLES"])
        except ValueError as e:
            issues.append(_issue("LAYER_ROLES_INVALID", f"GARC_LAYER_ROLES를 읽을 수 없습니다 — {e}"))
        else:
            if region is None and layer_roles:
                # 한 시트에 측면도·평면도가 같이 있으면 같은 레이어가 여러 뷰에 걸친다 → 영역 없이 레이어째 쓰면 평면도가 섞인다
                issues.append(_issue("LAYER_ROLES_INVALID", "레이어 단위 역할(GARC_LAYER_ROLES)은 측면도 영역(GARC_REGION)을 지정해야 쓸 수 있습니다.",
                                     sorted(set(layer_roles.values()))))
                layer_roles = {}

    # 2) 선박 정보
    ship: dict[str, Any] = {"z_base": 0.0, "eye_height": DEFAULT_EYE_HEIGHT}
    for key, (field, typ) in PROP_KEYS.items():
        if key in props and props[key] != "":
            try:
                ship[field] = parse_number(props[key]) if typ is float else props[key]
            except ValueError:
                issues.append(_issue("PROP_INVALID", f"{key}='{props[key]}'를 숫자로 읽을 수 없습니다."))

    conditions: list[dict[str, Any]] = []
    if props.get("GARC_CONDITIONS"):
        try:
            conditions = parse_conditions(props["GARC_CONDITIONS"])
        except ValueError as e:
            issues.append(_issue("CONDITIONS_INVALID", str(e), ["conditions"]))

    # 3) 비슷한 이름의 레이어 (예: GA-OBST, GA_OBST_CRANE, GA_0BST) — 그 객체들은 판정에 안 들어가므로 멈춘다
    layer_counts = raw.get("layer_counts") or {}
    if not isinstance(layer_counts, dict):
        raise ContractError("layer_counts는 {레이어 이름: 객체 수} 객체여야 합니다.")
    for name, count in sorted(layer_counts.items(), key=lambda kv: str(kv[0])):
        try:
            c = float(count)
            has_objects = not math.isfinite(c) or c > 0
        except (TypeError, ValueError, OverflowError):
            has_objects = True            # 개수를 해석할 수 없으면 '있다'고 보고 멈춘다
        known = near_miss_layer(name) if has_objects else None
        if known:
            issues.append(_issue("LAYER_NEAR_MISS",
                                 f"'{name}' 레이어의 객체({count}개)는 판정에 쓰지 않습니다 — 정식 이름 {known}과 비슷하지만 같지 않음. "
                                 f"{known} 레이어로 옮기거나 레이어 이름을 고치세요.", [LAYER_ROLES[known]]))

    # 4) 객체
    objects = []
    total_points = 0
    bad_roles: set[str] = set()
    # 레이어 단위 역할: 영역 안·밖 형상 객체 수. 매핑한 레이어는 모두 먼저 올려 두어, 형상이 하나도 없는 레이어(주석뿐·이름 틀림)도 잡는다
    layer_role_counts: dict[str, dict[str, Any]] = {ln: {"role": r, "in": 0, "out": 0} for ln, r in layer_roles.items()}
    for ent in raw.get("entities", []):
        layer = str(ent.get("layer", "")).strip().upper()
        source = "layer"
        if ent.get("role") is not None:                 # 실도면 역할 지정(객체별) — 레이어보다 우선
            role = ROLE_ALIASES.get(str(ent["role"]).strip().lower())
            source = "map"
            if role is None:
                bad_roles.add(str(ent["role"]))
                continue
        elif layer in LAYER_ROLES:
            role = LAYER_ROLES[layer]
        elif layer in layer_roles:
            role = layer_roles[layer]
            source = "layer_map"
        else:
            continue  # GA_ 규약 밖 레이어는 무시
        etype = str(ent.get("type", "")).strip().upper()
        if source == "layer_map" and etype in ANNOTATION_TYPES:
            continue  # 레이어째 지정한 경우 그 레이어의 문자·치수는 형상이 아니므로 뺀다
        if _has_non_finite(ent.get("points")):
            # NaN·무한대 좌표는 영역 안팎도 형상도 알 수 없다 → 어느 경로든(영역 밖으로 버리지 않고) 멈춘다
            _add_issue(issues, _issue("ENTITY_EMPTY",
                                      f"{layer} 레이어의 {etype} 객체(handle {ent.get('handle')}) 좌표가 수가 아닙니다(NaN·무한대). 다시 그리세요.",
                                      [role]))
            continue
        if source == "layer_map" and region is not None and etype not in SUPPORTED_TYPES:
            # 영역 밖(평면도 등)의 지원 안 되는 객체 때문에 멈추지 않게 — 영역 판정을 먼저(좌표가 없으면 안쪽으로 보고 멈춤).
            # 못 펼친 블록(INSERT)은 여기서 거르지 않는다: 삽입점만 전달되므로 삽입점이 영역 밖이어도 형상은 영역 안일 수 있다.
            try:
                raw_pts = [[float(p[0]), float(p[1])] for p in (ent.get("points") or [])]
            except (TypeError, ValueError, IndexError):
                raw_pts = []
            if raw_pts and not _in_region(raw_pts, region):
                layer_role_counts[layer]["out"] += 1
                continue
        if etype not in SUPPORTED_TYPES:
            _add_issue(issues, _issue("ENTITY_UNSUPPORTED",
                                      f"{layer} 레이어의 {etype} 객체(handle {ent.get('handle')})는 지원하지 않습니다. 선/폴리선/점으로 바꾸세요.",
                                      [role]))
            continue
        if etype == "INSERT" and role != "eye":
            # 블록은 삽입점 1개만 전달된다 → 장애물·선체·격벽 형상을 잃고 '기준 충족'으로 보일 수 있으므로 멈춘다.
            hint = (" 레이어 단위로 역할을 지정한 경우 측면도 영역 밖에 있어도 멈춥니다(삽입점만으로는 형상 위치를 알 수 없음) — "
                    "그 블록만 분해하거나, 레이어 지정 대신 필요한 객체만 골라 역할을 지정하세요(GARCROLE — 측면도 밖 블록은 고르지 않으면 됨)."
                    if source == "layer_map" else "")
            _add_issue(issues, _issue("ENTITY_UNSUPPORTED",
                                      f"{layer} 레이어의 블록(INSERT, handle {ent.get('handle')})은 삽입점만 읽혀 형상을 판정할 수 없습니다. "
                                      "블록을 분해(EXPLODE)하거나 폴리선으로 다시 그리세요. 블록은 GA_EYE(조종위치 점)에만 쓸 수 있습니다." + hint,
                                      [role]))
            continue
        try:
            pts = _entity_points(ent, ARC_TOL_M / scale if scale else None)
        except DegenerateArc as e:
            _add_issue(issues, _issue("ENTITY_EMPTY", f"{layer} 레이어의 {etype} 객체: {e}. 원(CIRCLE)이나 호 두 개로 그리세요.", [role]))
            continue
        if source == "layer_map":
            cnt = layer_role_counts.setdefault(layer, {"role": role, "in": 0, "out": 0})
            if region is not None and not _in_region(pts, region):
                cnt["out"] += 1
                continue  # 측면도 영역 밖(평면도·단면도 등) — 레이어 단위 역할에서만 거른다
            cnt["in"] += 1
        total_points += len(pts)
        if total_points > MAX_MODEL_POINTS:
            raise ContractError(f"GA_ 레이어 좌표가 너무 많습니다(호 분할 후 {MAX_MODEL_POINTS:,}점 초과) — "
                                "객체 수를 줄이거나 큰 호를 단순화하세요.")
        if scale is None:
            pts_m = []
        else:
            pts_m = [[_r(p[0] * scale), _r(p[1] * scale)] for p in pts]
        obj = {"handle": str(ent.get("handle")), "layer": layer, "role": role, "type": etype, "points": pts_m}
        if source != "layer":
            obj["source"] = source
        if ent.get("block") is not None:
            obj["block"] = str(ent["block"])
        objects.append(obj)
    for r in sorted(bad_roles):
        issues.append(_issue("ENTITY_ROLE_UNKNOWN", f"객체에 붙은 역할 '{r}'를 알 수 없습니다 — 플러그인과 엔진 버전을 확인하세요."))
    for lname, cnt in sorted(layer_role_counts.items()):
        if cnt["in"] == 0 and region is not None:
            # 영역을 잘못 잡았거나(예: m 값으로 입력) 레이어에 형상이 없으면(주석뿐·이름 틀림) 장애물이 없는 것처럼 보인다 → 멈춘다
            why = (f"객체 {cnt['out']}개가 모두 측면도 영역(GARC_REGION, 도면 단위) 밖입니다 — 영역을 다시 지정하세요"
                   if cnt["out"] else "형상 객체가 없습니다(문자·치수뿐이거나 레이어 이름이 다름) — 레이어를 다시 고르세요")
            issues.append(_issue("LAYER_ROLES_EMPTY", f"레이어 '{lname}'의 {why}.", [cnt["role"]]))

    model = {
        "schema": SCHEMA_MODEL,
        "units_scale": scale,
        "scale_n": scale_n,
        "layer_role_counts": layer_role_counts,
        # 입력이 없어 기본값을 쓴 선박 정보(준비 상태 표시·기선 대조용)
        "defaults": [f for f, key in (("z_base", "GARC_Z_BASE"), ("eye_height", "GARC_EYE_HEIGHT")) if not props.get(key)],
        "source": raw.get("source") or {},
        "ship": ship,
        "conditions": conditions,
        "objects": sorted(objects, key=lambda o: o["handle"]),
        "issues": issues,
    }
    validate_model(model)
    return model


def validate_model(model: dict[str, Any]) -> None:
    """구조 오류만 예외. 값의 모순은 issues에 추가해 규칙이 'review'로 멈추게 한다."""
    if not isinstance(model, dict):
        raise ContractError("model은 객체여야 합니다.")
    model.setdefault("schema", SCHEMA_MODEL)
    model.setdefault("ship", {})
    model.setdefault("conditions", [])
    model.setdefault("objects", [])
    model.setdefault("issues", [])
    ship = model["ship"]
    ship.setdefault("z_base", 0.0)
    ship.setdefault("eye_height", DEFAULT_EYE_HEIGHT)

    for o in model["objects"]:
        if o.get("role") not in ROLES:
            raise ContractError(f"알 수 없는 역할 '{o.get('role')}' (handle {o.get('handle')})")
        for p in o.get("points", []):
            if len(p) != 2 or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in p):
                raise ContractError(f"좌표 형식 오류 (handle {o.get('handle')}): {p}")

    issues = model["issues"]
    # 선박 숫자 값: NaN·무한대·0 이하 길이는 계산은 되지만 뜻이 없다 → 해당 값을 쓰는 판정을 멈춘다.
    bad_fields = set()
    for f in POSITIVE_FIELDS + FINITE_FIELDS + NONNEGATIVE_FIELDS:
        if ship.get(f) is None:
            continue
        v = ship[f]
        ok = _is_number(v) and math.isfinite(v)
        if ok and f in POSITIVE_FIELDS:
            ok = v > 0
        if ok and f in NONNEGATIVE_FIELDS:
            ok = v >= 0
        if ok and f == "eye_height":
            ok = v <= EYE_HEIGHT_MAX
        if not ok:
            bad_fields.add(f)
            rule_hint = "0보다 큰 유한한 m 값" if f in POSITIVE_FIELDS else (
                f"0~{EYE_HEIGHT_MAX:g} m — mm로 입력했는지 확인" if f == "eye_height" else "유한한 m 값")
            _add_issue(issues, _issue("SHIP_VALUE_INVALID",
                                      f"{FIELD_PROP.get(f, f)}={v!r}는 쓸 수 없습니다 ({rule_hint})."))

    # 길이 대조: L(만재흘수선 길이)은 LOA보다 길 수 없다. 자릿수 오타(1320, 13.2)는 범위를 크게 벗어난다.
    if not {"loa", "l_ll"} & bad_fields and ship.get("loa") is not None and ship.get("l_ll") is not None:
        loa, l_ll = ship["loa"], ship["l_ll"]
        tol = max(LENGTH_TOL_RATIO * loa, LENGTH_TOL_MIN)
        if l_ll > loa + tol or l_ll < L_LL_MIN_RATIO * loa:
            _add_issue(issues, _issue("SHIP_VALUE_MISMATCH",
                                      f"GARC_L_LL={l_ll:g} m와 GARC_LOA={loa:g} m가 맞지 않습니다 — L은 LOA보다 길 수 없고, "
                                      f"LOA의 {L_LL_MIN_RATIO:g}배보다 짧으면 자릿수 오타로 봅니다. 두 값을 확인하세요."))

    # AP–FP 간격 = LBP(정의). 크게 다르면 축척·기준점·LBP 중 하나가 틀렸다.
    if (ship.get("lbp") is not None and ship.get("x_ap") is not None and ship.get("x_fp") is not None
            and not {"lbp", "x_ap", "x_fp"} & bad_fields):
        span, lbp = ship["x_fp"] - ship["x_ap"], ship["lbp"]
        if abs(span - lbp) > max(LENGTH_TOL_RATIO * lbp, LENGTH_TOL_MIN):
            _add_issue(issues, _issue("SHIP_VALUE_MISMATCH",
                                      f"AP–FP 간격 {span:.2f} m가 LBP {lbp:g} m(GARC_LBP)와 다릅니다 — 축척(GARC_SCALE)·AP/FP 위치·LBP를 확인하세요."))

    if ("x_ap" in ship and "x_fp" in ship and not {"x_ap", "x_fp"} & bad_fields
            and ship["x_ap"] is not None and ship["x_fp"] is not None and ship["x_fp"] <= ship["x_ap"]):
        _add_issue(issues, _issue("PERP_ORDER",
                                  "FP가 AP보다 선수 방향(+x)에 있어야 합니다. 도면이 좌우 반전됐는지 확인하세요."))
    for c in model["conditions"]:
        for k in ("t_aft", "t_fwd"):
            v = c.get(k)
            if not _is_number(v) or not math.isfinite(v) or v < 0:
                _add_issue(issues, _issue("CONDITION_RANGE", f"흘수 조건 '{c.get('name')}'의 {k} 값이 올바르지 않습니다.", ["conditions"]))

    # 좌표가 하나도 없는 객체는 조용히 무시하지 않는다 (예: 추출기가 형상을 못 읽은 객체).
    # 단위를 몰라 좌표를 비운 경우는 UNIT_UNKNOWN이 이미 전부를 멈추므로 중복 경고하지 않는다.
    units_known = not any(i["code"] == "UNIT_UNKNOWN" for i in issues)
    for o in model["objects"]:
        where = f"{o.get('layer') or o['role']} 레이어의 객체(handle {o.get('handle')})"
        if units_known and not o.get("points"):
            _add_issue(issues, _issue("ENTITY_EMPTY", f"{where}에 좌표가 없습니다. 선/폴리선/점으로 다시 그리세요.", [o["role"]]))
        # 조종위치는 점 1개. 선·폴리선이면 첫 점만 쓰게 되므로 멈춘다.
        if o["role"] == "eye" and len(o.get("points") or []) > 1:
            _add_issue(issues, _issue("EYE_SHAPE", f"{where}: 조종위치(GA_EYE)는 점 1개(POINT 또는 블록)여야 합니다 "
                                      f"(좌표 {len(o['points'])}개).", ["eye"]))
        # model을 직접 받는 경로(서버 'model', CLI JSON)도 블록 규칙을 똑같이 적용
        if str(o.get("type", "")).upper() == "INSERT" and o["role"] != "eye":
            _add_issue(issues, _issue("ENTITY_UNSUPPORTED", f"{where}: 블록(INSERT)은 삽입점만 있어 형상을 판정할 수 없습니다. "
                                      "블록을 분해(EXPLODE)하거나 폴리선으로 다시 그리세요.", [o["role"]]))


def objects_by_role(model: dict[str, Any], role: str) -> list[dict[str, Any]]:
    return [o for o in model.get("objects", []) if o.get("role") == role]


def issues_for(model: dict[str, Any], roles: list[str]) -> list[dict[str, Any]]:
    wanted = set(roles)
    return [i for i in model.get("issues", []) if "*" in i["roles"] or wanted & set(i["roles"])]
