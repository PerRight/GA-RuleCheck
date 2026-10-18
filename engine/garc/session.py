"""Revision 관리: 변경 전후 diff, 영향 규칙 선별, 결과 캐시.

동기화 원칙 (멘토링 1차: add/delete/undo 뒤 결과 캐시 동기화)
- 플러그인은 '무엇이 바뀌었는지'를 엔진에 알려주지 않는다. 매번 GA_ 레이어 전체 스냅샷을 보낸다.
- 결과는 스냅샷의 순수 함수다. 캐시 키 = (규칙 ID, 그 규칙이 읽는 입력의 해시).
- 따라서 Undo/Redo로 이전 상태가 되면 해시가 같아져 이전 결과가 그대로 재사용된다.
  증분 갱신(+1/-1)을 하지 않으므로 이벤트를 놓쳐도 다음 스냅샷에서 자동으로 맞춰진다.
- diff는 '설명용'이다(어떤 객체가 바뀌어 어떤 규칙이 다시 돌았는지). 판정에는 쓰지 않는다.
"""
from __future__ import annotations

import copy
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .contract import validate_model
from .engine import Report, Rule, all_rules
from .readiness import readiness


def diff_models(prev: dict[str, Any] | None, curr: dict[str, Any]) -> dict[str, Any]:
    if prev is None:
        return {"initial": True, "added": [o["handle"] for o in curr["objects"]], "removed": [],
                "modified": [], "ship_changed": sorted(curr["ship"]), "conditions_changed": True,
                "changed_roles": sorted({o["role"] for o in curr["objects"]})}
    p = {o["handle"]: o for o in prev["objects"]}
    c = {o["handle"]: o for o in curr["objects"]}
    added = sorted(set(c) - set(p))
    removed = sorted(set(p) - set(c))
    modified = sorted(h for h in set(p) & set(c)
                      if (p[h]["role"], p[h]["points"]) != (c[h]["role"], c[h]["points"]))
    roles = {c[h]["role"] for h in added} | {p[h]["role"] for h in removed}
    roles |= {c[h]["role"] for h in modified} | {p[h]["role"] for h in modified}
    ship_changed = sorted(k for k in set(prev["ship"]) | set(curr["ship"])
                          if prev["ship"].get(k) != curr["ship"].get(k))
    return {"initial": False, "added": added, "removed": removed, "modified": modified,
            "ship_changed": ship_changed, "conditions_changed": prev["conditions"] != curr["conditions"],
            "changed_roles": sorted(roles)}


@dataclass
class Revision:
    number: int
    model: dict[str, Any]
    hashes: dict[str, str]
    report: Report
    diff: dict[str, Any] = field(default_factory=dict)
    transitions: list[dict[str, Any]] = field(default_factory=list)
    same_as: int | None = None
    time: str = ""


class Session:
    """문서(도면) 하나당 하나. 스레드 안전."""

    def __init__(self, rules: list[Rule] | None = None, max_revisions: int = 200):
        self.rules = rules or all_rules()
        self.revisions: list[Revision] = []
        self.cache: dict[tuple[str, str], Any] = {}
        self.max_revisions = max_revisions
        self._lock = threading.Lock()

    def check(self, model: dict[str, Any]) -> dict[str, Any]:
        validate_model(model)
        model = copy.deepcopy(model)
        with self._lock:
            prev = self.revisions[-1] if self.revisions else None
            results, hashes, rerun, cached = [], {}, [], []
            for rule in self.rules:
                h = rule.input_hash(model)
                hashes[rule.id] = h
                key = (rule.id, h)
                if key in self.cache:
                    res = copy.deepcopy(self.cache[key])
                    res.cached = True
                    cached.append(rule.id)
                else:
                    res = rule.evaluate(model)
                    self.cache[key] = copy.deepcopy(res)
                    rerun.append(rule.id)
                results.append(res)
            report = Report(results)

            affected = [r.id for r in self.rules if prev is None or prev.hashes.get(r.id) != hashes[r.id]]
            transitions = []
            if prev is not None:
                before = {r.rule_id: r.status for r in prev.report.results}
                for r in results:
                    if before.get(r.rule_id) != r.status:
                        transitions.append({"rule_id": r.rule_id, "from": before.get(r.rule_id), "to": r.status,
                                            "kind": _kind(before.get(r.rule_id), r.status)})
            same_as = next((rv.number for rv in reversed(self.revisions) if rv.hashes == hashes), None)

            diff = diff_models(prev.model if prev else None, model)
            rev = Revision(len(self.revisions) + 1 if not self.revisions else self.revisions[-1].number + 1,
                           model, hashes, report, diff=diff, transitions=transitions, same_as=same_as,
                           time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            self.revisions.append(rev)
            if len(self.revisions) > self.max_revisions:
                self.revisions.pop(0)

            out = report.to_dict()
            out.update({
                "revision": rev.number,
                "same_as_revision": same_as,     # Undo 등으로 이전과 동일한 상태면 그 revision 번호
                "diff": diff,
                "affected_rules": affected,      # 입력이 바뀐 규칙 (= '변경 → 규칙 자동 연결')
                "rerun_rules": rerun,            # 실제로 다시 계산한 규칙
                "cached_rules": cached,          # 캐시 재사용 규칙
                "transitions": transitions,      # 상태 변화 (new_violation / resolved / ...)
                "units_scale": model.get("units_scale"),
                "readiness": readiness(model),   # 판정에 필요한 입력의 준비 상태(실도면 연결)
            })
            return out


    @property
    def last(self) -> "Revision | None":
        return self.revisions[-1] if self.revisions else None

    def history(self) -> list[dict[str, Any]]:
        return [{"revision": r.number, "time": r.time, "overall": r.report.overall,
                 "same_as": r.same_as, "diff": r.diff, "transitions": r.transitions,
                 "summary": r.report.summary} for r in self.revisions]


def _kind(before: str | None, after: str) -> str:
    if after == "fail" and before != "fail":
        return "new_violation"
    if before == "fail" and after == "pass":
        return "resolved"
    if after in ("na", "review"):
        return "needs_human"
    return "changed"

