"""주요 치수표·축척 문자 읽기 — 실도면의 TEXT/MTEXT에서 LOA·LBP·폭·깊이·흘수·축척 '후보'를 찾는다.

결정론 규칙(정규식 + 같은 줄/같은 줄 번호 짝짓기)만 쓴다. AI·외부 전송 없음.
결과는 후보일 뿐 — 사람이 확인해 도면 특성(GARC_*)에 넣는다. L(ICLL)·밸러스트 흘수는 GA에 없으므로 찾지 않는다.

입력 texts: [{"text": "LENGTH O. A", "x": 500.0, "y": 150.0, "h": 3.0}, ...]  (도면 단위, MTEXT는 줄바꿈 포함 평문)
지원하는 표 모양 (실도면 5건에서 확인)
  1) 칸마다 TEXT: 'LENGTH O. A' ··· '251.00 M' 가 같은 높이(y)에 따로
  2) 여러 줄 MTEXT 두 개: 이름 열 'LENGTH O. A\\n\\nLENGTH B. P …' 과 값 열 '188.40M\\n\\n180.00M …' 이 같은 높이에 나란히
  3) 'SCALE' 근처 또는 따로 쓴 '1/1000'
"""
from __future__ import annotations

import math
import re
from typing import Any

# (키, 한글 이름, 이름 정규식) — 순서가 중요: 흘수 둘은 '설계'·'구조'로 구분
LABELS: list[tuple[str, str, re.Pattern[str]]] = [
    ("loa", "전장 LOA", re.compile(r"LENGTH\s*O\.?\s*A|L\.?\s*O\.?\s*A\b|LENGTH\s+OVER\s*ALL|전\s*장", re.I)),
    ("lbp", "수선간 길이 LBP", re.compile(r"LENGTH\s*B\.?\s*P|L\.?\s*B\.?\s*P\b|LENGTH\s+BETWEEN\s+PERP|수선간\s*(길이|장)", re.I)),
    ("breadth", "형폭 B", re.compile(r"BREADTH|형\s*폭", re.I)),
    ("depth", "형심 D", re.compile(r"^\s*DEPTH|형\s*심|깊이", re.I)),
    ("draft_design", "설계 흘수", re.compile(r"(DRAU?GHT|DRAFT).*DESIGN|설계\s*흘수", re.I)),
    ("draft_scantling", "구조 흘수", re.compile(r"(DRAU?GHT|DRAFT).*SCANT|구조\s*흘수", re.I)),
    ("scale", "축척", re.compile(r"^\s*SCALE\s*$|^\s*축\s*척\s*$", re.I)),
]
LABEL_KO = {k: ko for k, ko, _ in LABELS}
_VALUE = re.compile(r"^\s*([0-9]{1,4}(?:,[0-9]{3})*(?:\.[0-9]+)?)\s*(M|m|미터)?\s*$")
_SCALE = re.compile(r"(?:^|SCALE\s*[:=]?\s*|축척\s*)1\s*[/:]\s*([0-9]{1,5})\s*$", re.I)
_SHIP_TYPE = re.compile(r"\b(CONTAINER\s+(SHIP|VESSEL|CARRIER)|TANKER|BULK\s+CARRIER|CARRIER|PCTC|RO-?RO|FERRY)\b|컨테이너선|유조선|운반선", re.I)
# 그럴듯한 범위(오독 거르기용, 규정값 아님) — 밖이면 후보로 쓰지 않는다
MAX_VALUE = {"loa": 500.0, "lbp": 500.0, "breadth": 80.0, "depth": 50.0, "draft_design": 30.0, "draft_scantling": 30.0}
MIN_VALUE = {"loa": 10.0, "lbp": 10.0, "breadth": 2.0, "depth": 1.0, "draft_design": 0.5, "draft_scantling": 0.5}


def _lines(text: str) -> list[str]:
    return [ln.strip() for ln in str(text or "").replace("\r", "").split("\n")]


def _num(s: str) -> float | None:
    m = _VALUE.match(s)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


def _scale(s: str) -> float | None:
    m = _SCALE.search(s.strip())
    return float(m.group(1)) if m else None


def _label_key(line: str) -> str | None:
    for key, _, pat in LABELS:
        if pat.search(line):
            return key
    return None


def find_particulars(texts: list[dict[str, Any]]) -> dict[str, Any]:
    items = []
    for t in texts or []:
        try:
            x, y = float(t["x"]), float(t["y"])
            h = float(t.get("h") or 0.0)
        except (KeyError, TypeError, ValueError):
            continue
        if not (math.isfinite(x) and math.isfinite(y)):
            continue
        lines = _lines(t.get("text", ""))
        items.append({"x": x, "y": y, "h": h if math.isfinite(h) and h > 0 else 1.0, "lines": lines,
                      "full": [ln for ln in lines if ln]})
    found: dict[str, list[dict[str, Any]]] = {}

    def add(key: str, value: float, label: str, vtext: str, at: tuple[float, float], how: str) -> None:
        if key in MAX_VALUE and not (MIN_VALUE[key] <= value <= MAX_VALUE[key]):
            return
        found.setdefault(key, []).append({"value": value, "label": label, "text": vtext,
                                          "at": [round(at[0], 3), round(at[1], 3)], "how": how})

    for i, it in enumerate(items):
        keys = [_label_key(ln) for ln in it["full"]]
        if not any(keys):
            continue
        if len(it["full"]) > 1:
            # 모양 2) 여러 줄 이름 열 ↔ 같은 높이·오른쪽의 여러 줄 값 열, 비어 있지 않은 줄 번호로 짝
            cols = [o for j, o in enumerate(items) if j != i and o["x"] > it["x"]
                    and abs(o["y"] - it["y"]) <= max(it["h"], o["h"]) * 0.8 and len(o["full"]) == len(it["full"])]
            if cols:
                col = min(cols, key=lambda o: o["x"] - it["x"])
                pairs = [(k, lab, val, _scale(val) if k == "scale" else _num(val))
                         for k, lab, val in zip(keys, it["full"], col["full"]) if k]
                # 이름이 있는 줄의 값이 하나라도 숫자가 아니면 줄이 밀린 것(값 열에 'REV A' 같은 줄) → 이 열은 쓰지 않는다
                if pairs and all(v is not None for *_, v in pairs):
                    for k, lab, val, v in pairs:
                        add(k, v, lab, val, (col["x"], col["y"]), "표(여러 줄 문자 열)")
            continue
        k = keys[0]
        # 모양 1) 같은 줄(높이 차 ≤ 글자 높이 0.6배), 오른쪽에서 가장 가까운 값
        row = [o for j, o in enumerate(items) if j != i and len(o["full"]) == 1 and o["x"] > it["x"]
               and abs(o["y"] - it["y"]) <= max(it["h"], o["h"]) * 0.6 and o["x"] - it["x"] <= 60 * it["h"]]
        for o in sorted(row, key=lambda o: o["x"]):
            v = _scale(o["full"][0]) if k == "scale" else _num(o["full"][0])
            if v is not None:
                add(k, v, it["full"][0], o["full"][0], (o["x"], o["y"]), "표(같은 줄)")
                break
        else:
            if k == "scale":   # 'SCALE' 아래·오른쪽에 크게 쓴 '1/1000'(표제란)
                near = [o for j, o in enumerate(items) if j != i and len(o["full"]) == 1 and _scale(o["full"][0])
                        and 0 <= o["x"] - it["x"] <= 30 * it["h"] and abs(o["y"] - it["y"]) <= 6 * max(it["h"], o["h"])]
                if near:
                    o = min(near, key=lambda o: math.hypot(o["x"] - it["x"], o["y"] - it["y"]))
                    add("scale", _scale(o["full"][0]), it["full"][0], o["full"][0], (o["x"], o["y"]), "표제란(SCALE 근처)")

    # 모양 3) 이름 없이 따로 쓴 '1/1000'
    if "scale" not in found:
        for o in items:
            if len(o["full"]) == 1 and _scale(o["full"][0]):
                add("scale", _scale(o["full"][0]), "", o["full"][0], (o["x"], o["y"]), "축척 문자")

    out: dict[str, Any] = {}
    for key, ko, _ in LABELS:
        cands = found.get(key, [])
        values = sorted({c["value"] for c in cands})
        if not cands:
            out[key] = {"name_ko": ko, "status": "missing", "value": None, "candidates": []}
        elif len(values) == 1:
            out[key] = {"name_ko": ko, "status": "found", "value": values[0], "candidates": cands}
        else:
            # 값이 둘 이상이면 고르지 않는다(사람이 확인)
            out[key] = {"name_ko": ko, "status": "conflict", "value": None, "candidates": cands}

    ship_type = sorted((o for o in items if any(_SHIP_TYPE.search(ln) for ln in o["full"]) and len(o["full"]) == 1),
                       key=lambda o: -o["h"])
    out["ship_type"] = {"name_ko": "선종", "status": "found" if ship_type else "missing",
                        "value": ship_type[0]["full"][0] if ship_type else None, "candidates": []}

    # 도면 특성 제안(사람 확인 후 적용) — LOA·LBP·축척만. L(ICLL)·밸러스트 흘수는 GA에 없다.
    props: dict[str, str] = {}
    if out["loa"]["status"] == "found":
        props["GARC_LOA"] = f"{out['loa']['value']:g}"
    if out["lbp"]["status"] == "found":
        props["GARC_LBP"] = f"{out['lbp']['value']:g}"
    if out["scale"]["status"] == "found":
        props["GARC_SCALE"] = f"1/{out['scale']['value']:g}"
    drafts = [(k, out[k]["value"]) for k in ("draft_design", "draft_scantling") if out[k]["status"] == "found"]
    return {
        "schema": "garc.particulars/1",
        "items": out,
        "suggested_props": props,
        # 등흘수 조건 후보(선미=선수). 가시거리는 밸러스트(선수 흘수 작음)가 보통 더 불리하므로 그 조건은 사람이 넣는다.
        "suggested_conditions": [{"name": f"{LABEL_KO[k]}(등흘수)", "t_aft": v, "t_fwd": v} for k, v in drafts],
        "human_inputs_ko": ["L (만재흘수선협약 길이) — 선급 계산서 값, LBP와 다를 수 있음",
                            "밸러스트 등 실제 적하 상태의 선미·선수 흘수 — 가시거리에 가장 불리한 조건"],
    }
