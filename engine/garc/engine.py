"""규칙 평가와 보고서.

판정 상태
  pass   기준 충족 (규칙 후보 기준 — 선급 승인 아님)
  fail   기준 위반
  na     판정에 필요한 입력이 도면에 없음 (사람이 입력 보완)
  review 입력은 있으나 조건 밖·모순·모호 → 엔진이 판정을 멈추고 사람 확인 요청
  skip   적용 대상 아님 (예: LOA 55 m 미만)

전체 결과(overall): fail > incomplete(na/review 하나라도) > pass. 판정된 규칙이 없으면 na.
'일부 통과 + 일부 누락'을 초록(pass)으로 보여주지 않는 것이 핵심.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, asdict
from typing import Any, Callable

from .contract import validate_model, issues_for

STATUSES = ("pass", "fail", "na", "review", "skip")
STATUS_KO = {"pass": "기준 충족", "fail": "위반", "na": "입력 누락", "review": "검토 불가·사람 확인", "skip": "적용 제외"}
OVERALL_KO = {
    "pass": "규칙 후보 기준 충족 — 최종 판단은 설계자 검토",
    "fail": "규칙 후보 위반 있음 — 설계자 확인 필요",
    "incomplete": "검토 불가 항목 있음 — 사람 확인 전에는 통과로 보지 않음",
    "na": "판정된 규칙 없음",
}
DISCLAIMER_KO = "규칙 엔진의 후보 판정입니다. 선급 승인이나 적합 증명이 아니며, 최종 판단은 설계자·선급 검토로 합니다."
EPS = 1e-6  # m. 부동소수점 비교 보정용(경계값 포함 판정). 설계 허용오차가 아님.
ROLE_KO = {"hull": "선체 윤곽", "eye": "조종위치", "obstruction": "장애물", "collision_bhd": "충돌격벽", "bulb": "구상선수"}
ELEMENT_STATUSES = ("pass", "fail", "unknown")


def element(r: "Rule", obj: dict[str, Any], status: str, required: str, actual: str,
            diff: str | None = None, guide: str | None = None, margin: float | None = None) -> dict[str, Any]:
    """요소 판정 한 건. status는 pass/fail/unknown만 — '판정 못 함'은 unknown(⚠ 확인 필요)으로 통일."""
    assert status in ELEMENT_STATUSES, status
    return {"element_id": obj["handle"], "rule_id": r.id, "rule_name": r.title_ko, "role": obj["role"],
            "role_ko": ROLE_KO.get(obj["role"], obj["role"]), "status": status, "required": required,
            "actual": actual, "diff": diff, "guide": guide,
            "margin": None if margin is None else round(margin, 3)}


@dataclass
class RuleResult:
    rule_id: str
    title_ko: str
    ref: str
    status: str
    message_ko: str
    actual: float | None = None
    limit: Any = None
    unit: str = "m"
    condition: str | None = None          # 최악 흘수 조건 이름
    targets: list[str] = field(default_factory=list)       # 하이라이트할 CAD handle
    evidence: dict[str, Any] = field(default_factory=dict) # 수기 검산용 중간값
    limitations: list[str] = field(default_factory=list)   # 이 판정이 다루지 않는 범위
    suggestion_ko: str | None = None
    overlays: list[dict[str, Any]] = field(default_factory=list)  # 도면 위 임시 표시(시선 등), m 좌표
    # 요소(도면 객체)별 판정 — 마우스 오버 툴팁·체크리스트용. 상태는 pass / fail / unknown(확인 필요) 셋.
    # {element_id(handle), rule_id, rule_name, role, role_ko, status, required, actual, diff, guide, margin}
    elements: list[dict[str, Any]] = field(default_factory=list)
    cached: bool = False

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["status_ko"] = STATUS_KO[self.status]
        return d


@dataclass
class Rule:
    id: str
    title_ko: str
    ref: str
    edition: str
    roles: list[str]          # 이 규칙이 읽는 객체 역할(+ 'conditions' 의사 역할)
    ship_keys: list[str]      # 이 규칙이 읽는 ship 필드
    uses_conditions: bool
    fn: Callable[[dict[str, Any], "Rule"], RuleResult]
    limitations: list[str] = field(default_factory=list)
    formula_ko: str = ""      # 리포트·팔레트에 보여줄 판정식(수기 검산용)

    def input_view(self, model: dict[str, Any]) -> dict[str, Any]:
        """이 규칙의 판정에 영향을 주는 입력만 추린 것. 같으면 결과도 같다(캐시 키)."""
        roles = set(self.roles)
        return {
            "ship": {k: model["ship"].get(k) for k in self.ship_keys},
            "conditions": model["conditions"] if self.uses_conditions else None,
            "objects": [o for o in model["objects"] if o["role"] in roles],
            "issues": issues_for(model, self.roles),
            # 기본값을 쓴 항목(예: 기선 미입력 → 0)은 값이 같아도 판정이 다를 수 있다(기선 대조) → 캐시 키에 넣는다
            "defaults": sorted(set(model.get("defaults") or ()) & set(self.ship_keys)),
        }

    def input_hash(self, model: dict[str, Any]) -> str:
        blob = json.dumps(self.input_view(model), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]

    def evaluate(self, model: dict[str, Any]) -> RuleResult:
        blocking = issues_for(model, self.roles)
        if blocking:
            r = self.result("review", "; ".join(i["message_ko"] for i in blocking),
                            evidence={"issues": [i["code"] for i in blocking]})
            self._fill_unknown_elements(model, r)
            return r
        r = self.fn(model, self)
        r.limitations = list(self.limitations) + r.limitations
        self._fill_unknown_elements(model, r)
        return r

    def _fill_unknown_elements(self, model: dict[str, Any], r: RuleResult) -> None:
        """입력 누락·검토 불가면 이 규칙이 읽는 객체를 모두 '확인 필요'로 — 판정 안 된 객체가 파랑(충족)으로 보이지 않게."""
        if r.status not in ("na", "review") or r.elements:
            return
        why = r.message_ko if len(r.message_ko) <= 120 else r.message_ko[:117] + "…"
        r.elements = [element(self, o, "unknown", "판정에 필요한 입력이 갖춰져야 함", "판정 안 됨",
                              guide=why)
                      for o in model["objects"] if o["role"] in self.roles]

    def result(self, status: str, message_ko: str, **kw: Any) -> RuleResult:
        return RuleResult(rule_id=self.id, title_ko=self.title_ko, ref=self.ref,
                          status=status, message_ko=message_ko, **kw)


_REGISTRY: list[Rule] = []


def rule(**meta: Any):
    def deco(fn):
        _REGISTRY.append(Rule(fn=fn, **meta))
        return fn
    return deco


def all_rules() -> list[Rule]:
    from . import rules  # noqa: F401  등록 트리거
    return list(_REGISTRY)


@dataclass
class Report:
    results: list[RuleResult]

    @property
    def summary(self) -> dict[str, int]:
        c = {s: 0 for s in STATUSES}
        for r in self.results:
            c[r.status] += 1
        return c

    @property
    def overall(self) -> str:
        s = self.summary
        if s["fail"]:
            return "fail"
        if s["na"] or s["review"]:
            return "incomplete"
        if s["pass"] == 0:
            return "na"
        return "pass"

    def to_dict(self) -> dict[str, Any]:
        overall_ko = OVERALL_KO[self.overall]
        if self.overall == "pass" and self.summary["skip"]:
            # 적용 제외는 입력(LOA·용골 거치일)에 달려 있다 → 초록이어도 그 사실을 문구에 남긴다
            overall_ko += f" (적용 제외 {self.summary['skip']}건 — 적용 조건 입력 확인)"
        return {
            "overall": self.overall,
            "overall_ko": overall_ko,
            "summary": self.summary,
            "disclaimer_ko": DISCLAIMER_KO,
            "results": [r.to_dict() for r in self.results],
        }


def evaluate(model: dict[str, Any], rules: list[Rule] | None = None) -> Report:
    validate_model(model)
    return Report([r.evaluate(model) for r in (rules or all_rules())])
