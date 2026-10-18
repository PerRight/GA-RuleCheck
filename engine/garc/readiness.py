"""준비 상태 — 판정에 필요한 입력이 갖춰졌는지 한 번에 보여 준다(실도면 연결용).

규칙은 첫 누락에서 멈추므로('입력 누락: loa') 사용자는 다음 누락을 하나씩 알게 된다.
여기서는 모델을 보고 모든 필요 입력의 상태를 한글 이름으로 한꺼번에 낸다. 판정은 하지 않는다.

항목 상태
  ok        입력됨
  missing   없음 — 그 항목을 쓰는 규칙은 '입력 누락'
  invalid   있지만 쓸 수 없음(형식·범위·모순) — 그 규칙은 '검토 불가'
  default   입력이 없어 기본값을 씀(눈높이 1.8 m, 기선 z=0) — 도면 원점이 기선이 아니면 지정 필요
  optional  선택 항목이 비어 있음(없어도 판정은 됨)
"""
from __future__ import annotations

from typing import Any

from .contract import FIELD_PROP, objects_by_role

VIS, BHD = "SOLAS-V-22.1.1", "SOLAS-II1-12"

# (키, 한글 이름, 필수 여부, 쓰는 규칙, 어떻게 채우나)
SHIP_ITEMS = [
    ("loa", "전장 LOA", True, [VIS], "주요 치수표의 LENGTH O.A — 선박 정보 탭 또는 GARCLINK"),
    ("l_ll", "L (만재흘수선협약 길이)", True, [BHD], "선급 계산서의 L — 도면에 없으면 직접 입력(LBP와 다를 수 있음)"),
    ("x_ap", "AP 위치", True, [VIS, BHD], "도면에서 AP 클릭 — GARCPICK AP 또는 GARCLINK"),
    ("x_fp", "FP 위치", True, [VIS, BHD], "도면에서 FP 클릭 — GARCPICK FP 또는 GARCLINK"),
    ("z_base", "기선 높이", True, [VIS], "도면에서 기선 클릭 — GARCPICK BASE 또는 GARCLINK"),
    ("eye_height", "눈높이(선교 갑판 위)", False, [VIS], "기본 1.8 m"),
    ("lbp", "LBP (참고)", False, [], "주요 치수표의 LENGTH B.P — AP–FP 간격 대조용"),
    ("keel_laid", "용골 거치일", False, [VIS], "없으면 1998-07-01 이후 건조선으로 가정"),
]
ROLE_ITEMS = [
    ("hull", "선체 윤곽", True, [VIS], "측면도 선체 외곽선(갑판선·선수재·선미·바닥)에 역할 지정"),
    ("eye", "조종위치", True, [VIS], "선교 갑판 높이의 점 1개 — GARCEYE 또는 GARCLINK"),
    ("collision_bhd", "충돌격벽", True, [BHD], "측면도 충돌격벽 선에 역할 지정"),
    ("obstruction", "장애물(갑판 화물·크레인·마스트 등)", False, [VIS], "지정하지 않으면 선체 윤곽만으로 시야를 판정"),
    ("bulb", "구상선수", False, [BHD], "있는 배만 — 수선 아래 FP 앞으로 돌출한 부분"),
]
SHIP_ISSUE_CODES = ("PROP_INVALID", "SHIP_VALUE_INVALID", "SHIP_VALUE_MISMATCH", "PERP_ORDER")


def _fmt(v: Any) -> str:
    return f"{v:g} m" if isinstance(v, (int, float)) and not isinstance(v, bool) else str(v)


def readiness(model: dict[str, Any]) -> dict[str, Any]:
    ship = model.get("ship") or {}
    issues = model.get("issues") or []
    defaults = set(model.get("defaults") or ())
    items: list[dict[str, Any]] = []

    def add(key, name, required, rules, status, value=None, hint=None, problem=None):
        items.append({"key": key, "name_ko": name, "required": required, "rules": rules, "status": status,
                      "value_ko": value, "hint_ko": hint, "problem_ko": problem})

    # 단위·축척
    unit_issue = next((i for i in issues if i["code"] == "UNIT_UNKNOWN"), None)
    if unit_issue or model.get("units_scale") is None:
        add("units", "단위·축척", True, [VIS, BHD], "invalid" if unit_issue else "missing",
            hint="도면 단위(mm/m)와 축척(1/N) — GARCLINK", problem=unit_issue["message_ko"] if unit_issue else None)
    else:
        n = model.get("scale_n")
        add("units", "단위·축척", True, [VIS, BHD], "ok",
            value=f"도면 1단위 = {model['units_scale']:g} m" + (f" (축척 1/{n:g})" if n else ""))
    region_issue = next((i for i in issues if i["code"] in ("REGION_INVALID", "LAYER_ROLES_INVALID")), None)
    if region_issue:
        add("region", "측면도 영역·레이어 역할", False, [VIS, BHD], "invalid", problem=region_issue["message_ko"],
            hint="GARCLINK에서 측면도 영역을 다시 지정")

    # 선박 정보
    for key, name, required, rules, hint in SHIP_ITEMS:
        prop = FIELD_PROP.get(key, key)
        bad = next((i for i in issues if i["code"] in SHIP_ISSUE_CODES
                    and (prop in i["message_ko"] or (i["code"] == "PERP_ORDER" and key in ("x_ap", "x_fp")))), None)
        if bad:
            add(key, name, required, rules, "invalid", _fmt(ship[key]) if ship.get(key) is not None else None, hint, bad["message_ko"])
        elif key in defaults:
            add(key, name, required, rules, "default", f"기본값 {_fmt(ship.get(key))}", hint)
        elif ship.get(key) is None:
            add(key, name, required, rules, "missing" if required else "optional", None, hint)
        else:
            add(key, name, required, rules, "ok", _fmt(ship[key]), hint)

    # 흘수 조건
    conds = model.get("conditions") or []
    cond_issue = next((i for i in issues if i["code"] in ("CONDITIONS_INVALID", "CONDITION_RANGE")), None)
    names = ", ".join(c["name"] for c in conds)
    ballast = any(("밸러스트" in c["name"]) or ("ballast" in c["name"].lower()) for c in conds)
    if cond_issue:
        add("conditions", "흘수 조건", True, [VIS], "invalid", names or None, None, cond_issue["message_ko"])
    elif not conds:
        add("conditions", "흘수 조건", True, [VIS], "missing", None,
            "선박 정보 탭 흘수 조건 — 밸러스트 조건 포함(가시거리에 가장 불리한 경우가 많음)")
    else:
        add("conditions", "흘수 조건", True, [VIS], "ok", f"{len(conds)}개 ({names})",
            None if ballast else "밸러스트 조건이 없습니다 — 가시거리는 보통 밸러스트에서 가장 불리")

    # 도면 객체(역할)
    for role, name, required, rules, hint in ROLE_ITEMS:
        objs = objects_by_role(model, role)
        bad = next((i for i in issues if i["code"] in ("ENTITY_UNSUPPORTED", "ENTITY_EMPTY", "EYE_SHAPE", "LAYER_NEAR_MISS")
                    and role in i["roles"]), None)
        if bad:
            add(role, name, required, rules, "invalid", f"{len(objs)}개", hint, bad["message_ko"])
        elif not objs:
            add(role, name, required, rules, "missing" if required else "optional", None, hint)
        elif role == "eye" and len(objs) > 1:
            add(role, name, required, rules, "invalid", f"{len(objs)}개", hint, "조종위치는 하나만 있어야 합니다.")
        else:
            add(role, name, required, rules, "ok", f"{len(objs)}개", hint)

    req = [i for i in items if i["required"]]
    # 계약 문제 전부(항목에 붙지 않은 것 포함) — 하나라도 있으면 그 규칙은 '검토 불가'이므로 '준비 완료'로 보지 않는다
    problems = list(dict.fromkeys(i["message_ko"] for i in issues))
    return {
        "ready": sum(1 for i in req if i["status"] in ("ok", "default")),
        "total": len(req),
        "all_ready": all(i["status"] in ("ok", "default") for i in req) and not problems,
        "items": items,
        "problems_ko": problems,
    }
