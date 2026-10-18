"""SOLAS V/22.1.1 — 선교 가시거리(선수 전방 수면 사각 거리).

원문: "The view of the sea surface from the conning position shall not be obscured by more than
two ship lengths, or 500 m, whichever is less, forward of the bow to 10° on either side under all
conditions of draught, trim and deck cargo."
적용: 길이(V/2.4 = 전장 LOA) 55 m 이상, 1998-07-01 이후 건조선 (V/22.1 서두, MSC.142(77) 개정 기준).

계산 (측면도, 선체 중심선 단면)
  눈 E = (x_eye, z_eye),  z_eye = 조종위치 선교 갑판 높이 + 눈높이(기본 1.8 m)
  수면 z_wl(x) = z_base + t_aft + (t_fwd - t_aft)·(x - x_ap)/(x_fp - x_ap)   (트림 반영 직선)
  장애물 꼭짓점 O(눈보다 앞, 수면보다 위)마다 E→O 시선을 연장해 수면과 만나는 x_hit를 구한다.
  사각 거리 D = max(x_hit) - x_bow   (x_bow = 선체 윤곽의 최전단)
  기준: D ≤ min(2·LOA, 500 m). 모든 흘수 조건 중 최악값으로 판정.
  다각형 장애물의 최대 그림자는 반드시 꼭짓점에서 생기므로 꼭짓점만 보면 충분하다.
  (폴리선 호는 contract.py가 호에 외접하는 꺾은선으로 바꿔 두므로 그림자를 작게 보지 않는다 — 오차 ≤ 0.1 mm)

입력 대조(오타·단위 실수 → 'review'): 선체 길이 vs LOA, AP·FP가 선체 x 범위 안인지, 조종위치가 선수보다 뒤인지.
LOA를 잘못 넣어 55 m 미만으로 '적용 제외'되는 일이 없게, 이 대조는 적용 범위 판단보다 먼저 한다.
"""
from __future__ import annotations

import math
import re
from datetime import date
from typing import Any

from ..contract import objects_by_role
from ..engine import rule, Rule, RuleResult, EPS, element

# 오타 감지용 넓은 범위 (규정값이 아님)
KEEL_LAID_MIN = date(1950, 1, 1)   # 이보다 이른 용골 거치일은 오타로 본다(1926 → 2026 등)
EYE_Z_MAX_RATIO = 0.5              # 조종위치(선교 갑판) 높이 ≤ 기선 + 0.5·LOA
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}", re.ASCII)   # Python 버전마다 fromisoformat 허용 형식이 달라 직접 고정

LIMITATIONS = [
    "측면도(중심선)만 검토 — '좌우 10°' 범위와 평면상 장애물 폭은 미검토",
    "V/22.1.2~ 사각 섹터 각도(10°/20°/5°) 요건 미검토",
    "흘수 조건은 도면 특성에 입력된 것만 검토 — 모든 적재 조건 포함 여부는 사람 확인",
]


def _waterline(ship: dict[str, Any], cond: dict[str, Any]) -> tuple[float, float]:
    """z_wl(x) = a + b·x 의 (a, b)."""
    x_ap, x_fp = ship["x_ap"], ship["x_fp"]
    b = (cond["t_fwd"] - cond["t_aft"]) / (x_fp - x_ap)
    a = ship["z_base"] + cond["t_aft"] - b * x_ap
    return a, b


def blind_distance(eye: tuple[float, float], obstacles: list[tuple[str, float, float]],
                   x_bow: float, a: float, b: float) -> dict[str, Any]:
    """한 흘수 조건의 사각 거리. obstacles = [(handle, x, z)]."""
    ex, ez = eye
    best = {"x_hit": None, "handle": None, "point": None, "blocked": False}
    if ez <= a + b * ex:
        return {**best, "eye_below_water": True}
    for handle, ox, oz in obstacles:
        if ox <= ex + EPS:
            continue                      # 눈 뒤쪽·같은 위치는 전방 시야와 무관
        if oz <= a + b * ox + EPS:
            continue                      # 수면 아래 점은 시야를 가리지 않음
        if oz >= ez - EPS:
            return {"x_hit": math.inf, "handle": handle, "point": [ox, oz], "blocked": True}
        dx, dz = ox - ex, oz - ez
        denom = dz - b * dx
        if denom >= -EPS:                 # 시선이 수면과 평행/상승 → 수면에 닿지 않음
            return {"x_hit": math.inf, "handle": handle, "point": [ox, oz], "blocked": True}
        t = (a + b * ex - ez) / denom
        x_hit = ex + t * dx
        if best["x_hit"] is None or x_hit > best["x_hit"]:
            best = {"x_hit": x_hit, "handle": handle, "point": [ox, oz], "blocked": False}
    if best["x_hit"] is None:              # 앞에 아무것도 없으면 선수 끝이 곧 시야 시작
        best["x_hit"] = x_bow
    return best


def bottom_at(hull: list[dict[str, Any]], x: float) -> float | None:
    """선체 윤곽 선분들과 수직선 x의 교점 중 가장 낮은 z (교점이 없으면 None)."""
    zs = []
    for o in hull:
        pts = o["points"]
        for (x0, z0), (x1, z1) in zip(pts, pts[1:]):
            if min(x0, x1) - EPS <= x <= max(x0, x1) + EPS:
                if abs(x1 - x0) < EPS:
                    zs += [z0, z1]
                else:
                    zs.append(z0 + (z1 - z0) * (x - x0) / (x1 - x0))
    return min(zs) if zs else None


def max_height_at(eye: tuple[float, float], x_obj: float, x_target: float, a: float, b: float) -> float:
    """x_obj 위치 장애물이 x_target(=선수+허용거리)에서 수면에 닿는 시선을 넘지 않는 최대 높이."""
    ex, ez = eye
    z_target = a + b * x_target
    return ez + (x_obj - ex) * (z_target - ez) / (x_target - ex)


def _handle_list(model: dict[str, Any], handles: list[str], cap: int = 6) -> str:
    """제안 문구용 handle 목록 — 펼친 블록의 하위 객체는 '블록 1F3(하위 n개)'로 묶고, 많으면 줄인다(실도면 블록은 하위 객체가 수천 개)."""
    block_of = {o["handle"]: o.get("block") for o in model.get("objects", [])}
    tokens: list[str] = []
    seen: dict[str, int] = {}
    for h in handles:
        b = block_of.get(h)
        if b:
            if b not in seen:
                seen[b] = len(tokens)
                tokens.append(b)
            continue
        tokens.append(h)
    counts = {b: sum(1 for h in handles if block_of.get(h) == b) for b in seen}
    shown = [f"블록 {t}(하위 {counts[t]}개)" if t in counts else t for t in tokens[:cap]]
    more = f" 외 {len(tokens) - cap}개" if len(tokens) > cap else ""
    return "handle " + ", ".join(shown) + more


def _floor_cm(v: float) -> float:
    """'이하로' 제안값은 내림(그대로 적용해도 기준을 넘지 않게). 1e-7 cm 여유는 부동소수점 잡음 흡수용."""
    return math.floor(v * 100 + 1e-7) / 100


def _element_checks(r: Rule, objs: list[dict[str, Any]], eye_obj: dict[str, Any], eye: tuple[float, float],
                    x_bow: float, limit: float, per_cond: list[dict[str, Any]], d: float, ok: bool,
                    worst_cond: str, suggestion: str | None) -> list[dict[str, Any]]:
    """객체마다 '이 객체 하나만으로' 사각 거리 기준을 넘는지. 규칙 판정과 같은 계산(blind_distance)을 써서
    규칙이 위반이면 반드시 위반 객체가 하나 이상 나오고, 규칙이 충족이면 위반 객체가 없다."""
    ex, ez = eye
    x_target = x_bow + limit
    out = []
    for o in objs:
        pts = [(o["handle"], p[0], p[1]) for p in o["points"]]
        fwd = [(px, pz) for _, px, pz in pts if px > ex + EPS]
        if not fwd:
            out.append(element(r, o, "pass", "선교보다 앞에 있는 객체만 전방 시야를 가림", "선교 뒤쪽 — 전방 시야와 무관"))
            continue
        d_o = max(blind_distance(eye, pts, x_bow, *c["_ab"])["x_hit"] - x_bow for c in per_cond)
        bad = d_o > limit + EPS
        # 설명용 수치: 가장 여유가 적은 꼭짓점의 허용 상단(입력된 모든 흘수 조건 중 최솟값)
        margin, px, pz = min((min(max_height_at(eye, x, x_target, *c["_ab"]) for c in per_cond) - z, x, z) for x, z in fwd)
        z_lim = min(pz + margin, ez - 0.01)
        req = f"상단 z ≤ {_floor_cm(z_lim):.2f} m (x={px:.2f} m 지점, 입력된 모든 흘수 조건)"
        if bad:
            exceed = "눈높이 이상 — 시선이 수면에 닿지 않음" if math.isinf(d_o) else f"이 객체만으로 사각 {d_o:.2f} m > 허용 {limit:g} m"
            out.append(element(r, o, "fail", req, f"z = {pz:.2f} m ({exceed})",
                               diff=f"{max(-margin, 0.0):.2f} m 초과",
                               guide=f"x={px:.2f} m 지점 상단을 z={_floor_cm(z_lim):.2f} m 이하로 낮추세요", margin=margin))
        else:
            out.append(element(r, o, "pass", req, f"z = {pz:.2f} m (여유 {max(margin, 0.0):.2f} m)", margin=margin))
    # 조종위치는 '판정의 기준점'이지 위반 대상이 아니다 — 입력이 유효하면(여기까지 왔으면) 충족으로 두고,
    # 이 눈높이에서의 사각 거리를 참고로 보여 준다. 위반 개수는 장애물·선체·격벽 요소로만 센다.
    seen = "시선이 수면에 닿지 않음" if math.isinf(d) else f"사각 {d:.2f} m / 허용 {limit:g} m (최악: {worst_cond})"
    out.append(element(r, eye_obj, "pass", "조종위치 1개 · 선체 범위 안 · 선수보다 뒤",
                       f"눈높이 z = {ez:.2f} m · 이 눈높이에서 {seen}",
                       guide=None if ok else "앞 장애물을 낮추기 어렵다면 선교(조종위치)를 높이는 것도 방법입니다."))
    return out


def _label(at, text, loa, status="info"):
    return {"kind": "label", "status": status, "points": [list(at)], "text": text, "height": round(max(loa * 0.012, 0.5), 3)}


def _overlays(eye, x_bow, limit, d, ok, worst, ship, loa):
    """도면 위 임시 표시(m 좌표). 플러그인·리포트 SVG가 같은 데이터를 그린다."""
    a, b = worst["_ab"]
    st = "pass" if ok else "fail"
    x_lim = x_bow + limit
    hit_x = worst["_bd"]["x_hit"] if not math.isinf(d) else x_lim * 1.15
    x_end = max(hit_x, x_lim) + loa * 0.1
    out = [
        {"kind": "waterline", "status": "info", "points": [[ship["x_ap"], a + b * ship["x_ap"]], [x_end, a + b * x_end]]},
        _label([ship["x_ap"], a + b * ship["x_ap"] - loa * 0.03], f"수선 · {worst['condition']} (선미 {worst['t_aft']:g} / 선수 {worst['t_fwd']:g} m)", loa),
        {"kind": "limit_marker", "status": "info", "points": [[x_lim, a + b * x_lim], [x_lim, eye[1]]]},
        _label([x_lim, eye[1]], f"허용 한계 {limit:g} m", loa),
        {"kind": "eye", "status": "info", "points": [list(eye)]},
        _label([eye[0], eye[1] + loa * 0.015], f"조종위치 눈높이 z={eye[1]:.2f} m", loa),
    ]
    if worst["_bd"]["point"] is not None:
        out.append({"kind": "sightline", "status": st, "points": [list(eye), [hit_x, a + b * hit_x]]})
        txt = "시선이 수면에 닿지 않음" if math.isinf(d) else f"사각 {d:.1f} m"
        out.append(_label([hit_x, a + b * hit_x + loa * 0.015], txt, loa, st))
    return out


@rule(id="SOLAS-V-22.1.1", title_ko="선교 전방 수면 가시거리",
      ref="SOLAS Ch.V Reg.22.1.1", edition="SOLAS 통합본 (V/22: MSC.142(77) 개정 반영)",
      roles=["hull", "eye", "obstruction", "conditions"],
      ship_keys=["loa", "x_ap", "x_fp", "z_base", "eye_height", "keel_laid"],
      uses_conditions=True, limitations=LIMITATIONS,
      formula_ko="D = max(x_hit) − x_bow ≤ min(2·LOA, 500 m),  x_hit = x_e + t·(x_o − x_e),  "
                 "t = (a + b·x_e − z_e) / ((z_o − z_e) − b·(x_o − x_e)),  수선 z = a + b·x,  z_e = 선교갑판 + 1.8 m")
def check_visibility(model: dict[str, Any], r: Rule) -> RuleResult:
    ship = model["ship"]
    missing = [k for k in ("loa", "x_ap", "x_fp") if ship.get(k) is None]
    if missing:
        return r.result("na", f"선박 정보 누락: {', '.join(missing)} (도면 특성 GARC_LOA/GARC_X_AP/GARC_X_FP)")
    loa = ship["loa"]
    hull = objects_by_role(model, "hull")
    hull_x = [p[0] for o in hull for p in o["points"]]

    # 입력 대조 — 적용 범위(LOA < 55 m '적용 제외')보다 먼저: LOA 오타가 '적용 제외 → 초록'으로 새지 않게
    if hull_x:
        tol_len = max(0.02 * loa, 0.5)
        span = max(hull_x) - min(hull_x)
        if abs(span - loa) > tol_len:
            return r.result("review",
                            f"도면 선체 길이 {span:.2f} m와 LOA {loa:g} m가 다릅니다 — 단위·축척 또는 LOA 입력 확인.",
                            evidence={"hull_span": round(span, 3), "loa": loa})
        if not (min(hull_x) - tol_len <= ship["x_ap"] and ship["x_fp"] <= max(hull_x) + tol_len):
            return r.result("review",
                            f"AP(x={ship['x_ap']:g} m)·FP(x={ship['x_fp']:g} m)가 도면 선체 범위 "
                            f"{min(hull_x):.2f}~{max(hull_x):.2f} m 밖입니다 — GARC_X_AP/GARC_X_FP 확인.",
                            evidence={"hull_x_range": [round(min(hull_x), 3), round(max(hull_x), 3)]})

    # 적용 범위
    if loa < 55.0 - EPS:
        return r.result("skip", f"LOA {loa:g} m < 55 m — V/22.1 적용 대상 아님", evidence={"loa": loa})
    notes = []
    if ship.get("keel_laid"):
        text = str(ship["keel_laid"]).strip()
        try:
            if not _DATE_RE.fullmatch(text):
                raise ValueError(text)
            laid = date.fromisoformat(text)
        except ValueError:
            return r.result("review", f"용골 거치일 형식 오류: {text} (YYYY-MM-DD)")
        if laid < KEEL_LAID_MIN:
            return r.result("review", f"용골 거치일 {text}이 {KEEL_LAID_MIN.year}년보다 이릅니다 — 오타인지 확인하세요"
                                      " (적용 제외로 넘기지 않고 멈춤, 오타 감지용 범위).")
        if laid < date(1998, 7, 1):
            return r.result("skip", f"1998-07-01 이전 건조선(용골 거치일 {text}) — V/22.1 적용 대상 아님")
    else:
        notes.append("용골 거치일 미입력 — 1998-07-01 이후 건조선으로 가정")

    eyes = objects_by_role(model, "eye")
    if not eyes:
        return r.result("na", "조종위치(GA_EYE 레이어의 점)가 없습니다.")
    if len(eyes) > 1:
        return r.result("review", f"조종위치가 {len(eyes)}개입니다. 하나만 남기세요.",
                        targets=[e["handle"] for e in eyes])
    if not eyes[0]["points"]:
        return r.result("na", "조종위치 점 좌표가 없습니다.")
    if not hull:
        return r.result("na", "선체 윤곽(GA_HULL)이 없어 선수 끝 위치를 알 수 없습니다.")
    if not model["conditions"]:
        return r.result("na", "흘수 조건이 없습니다 (GARC_CONDITIONS, 예: 밸러스트 도착:6.5/4.0).")

    x_bow = max(hull_x)
    hull_z = [p[1] for o in hull for p in o["points"]]
    tol_len = max(0.02 * loa, 0.5)
    ex, ez0 = eyes[0]["points"][0]
    # 조종위치 대조(오타 감지용): 선체 x 범위 안 + 선수 끝보다 뒤, 기선 위 0.5·LOA 이하
    if ex >= x_bow - EPS or ex < min(hull_x) - tol_len:
        return r.result("review", f"조종위치(x={ex:.2f} m)가 선체 범위 {min(hull_x):.2f} m ~ 선수 끝 {x_bow:.2f} m 사이에 있지 않습니다 "
                                  "— GA_EYE 위치 확인.", targets=[eyes[0]["handle"]])
    if ez0 - ship["z_base"] > EYE_Z_MAX_RATIO * loa:
        return r.result("review", f"조종위치 높이 z={ez0:.2f} m가 기선 위 {EYE_Z_MAX_RATIO:g}·LOA({EYE_Z_MAX_RATIO * loa:.1f} m)를 넘습니다 "
                                  "— GA_EYE 위치·단위 확인(오타 감지용 범위).", targets=[eyes[0]["handle"]])
    # 흘수·기선 대조(오타 감지용): 기선은 선체 바닥보다 위로 크게 벗어날 수 없고, 수선은 선체 최고점보다 낮아야 한다.
    # 바닥은 AP~FP 가운데 절반 구간에서 본다 — 선미가 깊은 레이크드 킬·선수 포어풋 때문에 정상 도면이 막히지 않게.
    # 21점의 '가장 낮은 선' 중 중앙값을 쓴다 — 바닥선이 군데군데 끊긴 도면에서 한 점만 갑판선으로 튀어도 대조가 꺼지지 않게.
    # (중앙부 바닥선이 절반 이상 없으면 이 대조는 약해진다 — input-contract에 명시)
    span_pp = ship["x_fp"] - ship["x_ap"]
    mids = [bottom_at(hull, ship["x_ap"] + span_pp * (0.25 + 0.5 * k / 20)) for k in range(21)]
    mids = sorted(z for z in mids if z is not None)
    mid_bottom = mids[len(mids) // 2] if mids else None
    if mid_bottom is not None and ship["z_base"] > mid_bottom + tol_len:
        return r.result("review", f"기선 z={ship['z_base']:g} m(GARC_Z_BASE)가 도면 선체 중앙부 바닥 z={mid_bottom:.2f} m보다 "
                                  f"{tol_len:.1f} m 넘게 높습니다 — 기선 높이 확인.")
    # 기선이 선체 최저점보다 한참 아래면(원점이 기선이 아닌 실도면에서 기선을 안 넣어 기본값 0을 쓰거나 GARCSETUP의 0이 남은 경우),
    # 수선이 배 아래로 내려가 시야가 실제보다 좋게 계산된다 → 통과로 새지 않게 멈춘다.
    # (선체 윤곽에 바닥·용골 쪽 선이 하나도 없으면 정상 도면도 여기서 멈춘다 — 안전 쪽. 선체에 바닥선을 포함하면 풀린다.)
    if min(hull_z) > ship["z_base"] + tol_len:
        why = "입력되지 않아 기본값 0을 씁니다" if "z_base" in model.get("defaults", ()) else f"z={ship['z_base']:g} m입니다"
        return r.result("review", f"기선(GARC_Z_BASE)이 {why} — 도면 선체 최저점 z={min(hull_z):.2f} m보다 {tol_len:.1f} m 넘게 아래. "
                                  "도면 원점이 기선이 아니면 기선을 도면에서 지정하고(GARCPICK BASE), 선체 윤곽에 바닥선을 포함하세요.")
    for cond in model["conditions"]:
        top = ship["z_base"] + max(cond["t_aft"], cond["t_fwd"])
        if top > max(hull_z) + EPS:
            return r.result("review", f"'{cond['name']}' 조건의 수선 z={top:.2f} m(기선+흘수)가 도면 선체 최고점 z={max(hull_z):.2f} m보다 "
                                      "높습니다 — 흘수·기선 입력 확인.")
    eye = (ex, ez0 + ship["eye_height"])
    obstacles = [(o["handle"], p[0], p[1]) for o in hull + objects_by_role(model, "obstruction") for p in o["points"]]
    limit = min(2.0 * loa, 500.0)

    per_cond = []
    for cond in model["conditions"]:
        a, b = _waterline(ship, cond)
        bd = blind_distance(eye, obstacles, x_bow, a, b)
        if bd.get("eye_below_water"):
            return r.result("review", f"'{cond['name']}' 조건에서 눈높이가 수면 아래입니다 — 입력 확인.")
        d = bd["x_hit"] - x_bow
        per_cond.append({"condition": cond["name"], "t_aft": cond["t_aft"], "t_fwd": cond["t_fwd"],
                         "blind_distance": None if math.isinf(d) else round(d, 3),
                         "critical_handle": bd["handle"], "critical_point": bd["point"],
                         "_d": d, "_ab": (a, b), "_bd": bd})

    if any(math.isnan(c["_d"]) for c in per_cond):   # 계약 검증을 통과했다면 생기지 않음 — 방어용
        return r.result("review", "사각 거리 계산값이 숫자가 아닙니다 — 선박 정보·흘수 입력을 확인하세요.")
    worst = max(per_cond, key=lambda c: c["_d"])
    d = worst["_d"]
    ok = d <= limit + EPS
    a, b = worst["_ab"]
    evidence = {
        "eye": [round(eye[0], 3), round(eye[1], 3)], "x_bow": round(x_bow, 3), "loa": loa,
        "limit_rule": "min(2·LOA, 500 m)", "notes": notes,
        "conditions": [{k: v for k, v in c.items() if not k.startswith("_")} for c in per_cond],
    }

    overlays = _overlays(eye, x_bow, limit, d, ok, worst, ship, loa)

    judged = hull + objects_by_role(model, "obstruction")
    if ok:
        msg = f"사각 거리 {d:.2f} m ≤ 허용 {limit:g} m (최악: {worst['condition']})"
        return r.result("pass", msg, actual=round(d, 3), limit=limit, condition=worst["condition"],
                        evidence=evidence, overlays=overlays, targets=[],
                        elements=_element_checks(r, judged, eyes[0], eye, x_bow, limit, per_cond, d, True,
                                                 worst["condition"], None))

    crit_h = worst["critical_handle"]
    x_target = x_bow + limit

    def z_limit(x: float) -> tuple[float, str]:
        """x 위치에서 입력된 '모든' 흘수 조건을 만족하는 최대 높이와 그것을 정한 조건."""
        return min(((max_height_at(eye, x, x_target, *c["_ab"]), c["condition"]) for c in per_cond), key=lambda t: t[0])

    # 제안 대상 꼭짓점: 결정 장애물의 꼭짓점 중 허용 높이를 넘는 것 가운데 가장 엄격한 것.
    # (사각 거리가 무한대일 땐 처음 만난 꼭짓점이 결정 꼭짓점이 되므로 그것만 보면 순서에 따라 제안이 달라진다)
    viol = []
    for o in hull + objects_by_role(model, "obstruction"):
        if o["handle"] != crit_h:
            continue
        for px, pz in o["points"]:
            if px <= ex + EPS:
                continue
            zl, cname = z_limit(px)
            if pz > zl + EPS:
                viol.append((zl, px, cname))
    if viol:
        zmax, ox, gov_cond = min(viol)
    else:   # 이론상 생기지 않음 — 결정 꼭짓점 기준
        ox = worst["critical_point"][0]
        zmax, gov_cond = z_limit(ox)
    if not math.isfinite(zmax):   # 입력 검증을 통과했다면 생기지 않음 — 방어용
        return r.result("review", "허용 높이 계산값이 유한하지 않습니다 — 조종위치·흘수 입력을 확인하세요.",
                        targets=[crit_h])
    # 선수 트림이 커서 연장 수선이 눈보다 높아지면 z_lim이 눈높이를 넘을 수 있다 → 눈높이 아래로 제한
    # (눈높이 이상 장애물은 시선이 수면에 닿지 않으므로, 제안은 항상 눈보다 낮아야 한다)
    zmax = min(zmax, eye[1] - 0.01)
    z_by_cond = [(c["condition"], max_height_at(eye, ox, x_target, *c["_ab"])) for c in per_cond]
    # 표시값은 안전 쪽으로 내림(그대로 적용해도 기준을 넘지 않게). 1e-7 cm 여유는 부동소수점 잡음 흡수용.
    zmax_disp = math.floor(zmax * 100 + 1e-7) / 100
    # 결정 장애물 말고도 혼자서 허용 한계를 넘는 객체가 있으면 함께 알린다(제안 하나로 끝나지 않음).
    others = []
    for o in hull + objects_by_role(model, "obstruction"):
        if o["handle"] == crit_h:
            continue
        pts = [(o["handle"], p[0], p[1]) for p in o["points"]]
        for c in per_cond:
            bd_o = blind_distance(eye, pts, x_bow, *c["_ab"])
            if bd_o["x_hit"] - x_bow > limit + EPS:
                others.append(o["handle"])
                break
    sugg = (f"x={ox:.2f} m 장애물(handle {crit_h}) 상단을 z={zmax_disp:.2f} m 이하로 낮추거나"
            f"(입력된 모든 흘수 조건 기준, 가장 엄격한 조건: '{gov_cond}'), 선수 트림을 줄이거나, 선교 높이를 올리세요.")
    if others:
        sugg += f" 다른 객체({_handle_list(model, others)})도 단독으로 허용 한계를 넘으므로 함께 조정해야 합니다."
    if math.isinf(d):
        msg = f"장애물이 눈높이 이상 — 시선이 수면에 닿지 않음 (허용 {limit:g} m, {worst['condition']})"
        actual = None
    else:
        msg = f"사각 거리 {d:.2f} m > 허용 {limit:g} m (최악: {worst['condition']})"
        actual = round(d, 3)
    evidence["max_obstacle_z_at_critical_x"] = round(zmax, 3)
    evidence["suggestion_x"] = round(ox, 3)
    evidence["max_obstacle_z_by_condition"] = [{"condition": n, "z_max": round(z, 3)} for n, z in z_by_cond]
    evidence["suggestion_governing_condition"] = gov_cond
    if others:
        evidence["other_violating_handles"] = others
    overlays.append(_label([ox, zmax], f"허용 상단 z ≤ {zmax_disp:.2f} m", loa, "fail"))
    overlays.append({"kind": "tick", "status": "fail", "points": [[ox - loa * 0.02, zmax], [ox + loa * 0.02, zmax]]})
    return r.result("fail", msg, actual=actual, limit=limit, condition=worst["condition"],
                    targets=[crit_h] + others, evidence=evidence, overlays=overlays, suggestion_ko=sugg,
                    elements=_element_checks(r, judged, eyes[0], eye, x_bow, limit, per_cond, d, False,
                                             worst["condition"], sugg))
