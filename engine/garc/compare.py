"""버전 비교 — 저장된 두 도면 스냅샷(raw)의 차이를 '사람이 읽는 변경 항목'으로 바꾼다.

플러그인(비교 창·생산팀 전달 내보내기)은 이 결과를 그리기만 한다. 무엇이 바뀌었는지, 그 변경으로
어떤 위반이 해결·발생했는지는 여기서(엔진) 계산한다 — '판정·diff는 엔진만' 원칙.

변경 항목(changes) 한 건
  number      ① ② … 순번(선미→선수 순, 형상 없는 선박 정보·흘수 조건은 끝)
  kind        added / removed / modified / ship / conditions
  element_id  handle (형상 변경일 때)
  role, role_ko
  desc_ko     한 줄 설명 (예: "충돌격벽 위치 x 124.50 → 122.50 m (기준점에서 9.00 → 11.00 m)")
  before_ko / after_ko  변경 전·후 값
  resolved    이번 변경으로 해결된 위반 [{rule_id, rule_name, text}] — 이후가 '충족'이거나 객체가 삭제된 경우만
  introduced  이번 변경으로 새로 생긴 위반
  needs_check 이번 변경으로 판정 못 함(확인 필요)·적용 제외로 바뀐 것 — '해결'로 세지 않는다(모르면 멈춘다)
  related_rules  이 변경이 입력으로 들어가는 규칙
  center, bbox   표식 위치(m) — 플러그인은 units_scale로 나눠 도면 좌표로 바꾼다
"""
from __future__ import annotations

import math
from typing import Any

from .contract import issues_for, raw_to_model
from .engine import Report, all_rules, evaluate, ROLE_KO
from .session import diff_models

SHIP_KO = {"loa": "LOA", "l_ll": "L(만재흘수선협약)", "x_ap": "AP 위치 x", "x_fp": "FP 위치 x",
           "z_base": "기선 높이 z", "eye_height": "눈높이", "keel_laid": "용골 거치일", "ship_type": "선종",
           "lbp": "LBP"}
_KIND_ORDER = {"added": 0, "removed": 0, "modified": 0, "ship": 1, "conditions": 2}


def _stats(obj: dict[str, Any] | None) -> dict[str, float] | None:
    if not obj or not obj.get("points"):
        return None
    xs = [p[0] for p in obj["points"]]
    zs = [p[1] for p in obj["points"]]
    return {"x0": min(xs), "x1": max(xs), "z0": min(zs), "z1": max(zs)}


def _shape_ko(role: str, st: dict[str, float] | None) -> str:
    if st is None:
        return "좌표 없음"
    if role == "eye":
        return f"x {st['x0']:.2f}, z {st['z1']:.2f} m"
    if role == "collision_bhd":
        return f"x {st['x0']:.2f} m" if abs(st["x1"] - st["x0"]) < 1e-3 else f"x {st['x0']:.2f}~{st['x1']:.2f} m"
    return f"x {st['x0']:.1f}~{st['x1']:.1f} m, 상단 z {st['z1']:.2f} m"


def _bhd_distance(report: Report) -> str | None:
    for r in report.results:
        if r.rule_id == "SOLAS-II1-12" and "d_min" in r.evidence:
            e = r.evidence
            return f"{e['d_min']:.2f} m" if abs(e["d_max"] - e["d_min"]) < 1e-3 else f"{e['d_min']:.2f}~{e['d_max']:.2f} m"
    return None


def _modified_desc(role: str, a: dict[str, float] | None, b: dict[str, float] | None,
                   rep_a: Report, rep_b: Report) -> str:
    rk = ROLE_KO.get(role, "객체")
    if a is None or b is None:
        return f"{rk} 형상 변경"
    if role == "eye":
        return f"조종위치 (x {a['x0']:.2f}, z {a['z1']:.2f}) → (x {b['x0']:.2f}, z {b['z1']:.2f}) m"
    if role == "collision_bhd":
        text = f"충돌격벽 위치 {_shape_ko(role, a)} → {_shape_ko(role, b)}"
        da, db = _bhd_distance(rep_a), _bhd_distance(rep_b)
        if da and db and da != db:
            text += f" (기준점에서 {da} → {db})"
        return text
    parts = []
    if abs(b["z1"] - a["z1"]) > 1e-3:
        parts.append(f"상단 z {a['z1']:.2f} → {b['z1']:.2f} m")
    if abs(b["x0"] - a["x0"]) > 1e-3 or abs(b["x1"] - a["x1"]) > 1e-3:
        parts.append(f"x {a['x0']:.1f}~{a['x1']:.1f} → {b['x0']:.1f}~{b['x1']:.1f} m")
    if abs(b["z0"] - a["z0"]) > 1e-3 and not parts:
        parts.append(f"하단 z {a['z0']:.2f} → {b['z0']:.2f} m")
    return f"{rk} " + (", ".join(parts) if parts else "형상 변경(범위 같음)")


def _fmt_conditions(conds: list[dict[str, Any]]) -> str:
    return "; ".join(f"{c['name']} {c['t_aft']:g}/{c['t_fwd']:g}" for c in conds) or "없음"


def _elements_by_handle(report: Report) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for r in report.results:
        for e in r.elements:
            out.setdefault(e["element_id"], []).append(e)
    return out


def _status_of(els: list[dict[str, Any]] | None, rule_id: str) -> str | None:
    for e in els or []:
        if e["rule_id"] == rule_id:
            return e["status"]
    return None


def _version_summary(label: str, model: dict[str, Any], report: Report) -> dict[str, Any]:
    d = report.to_dict()
    els = [e for r in report.results for e in r.elements]
    return {
        "label": label, "overall": d["overall"], "overall_ko": d["overall_ko"], "summary": d["summary"],
        "fail_elements": sum(1 for e in els if e["status"] == "fail"),
        "unknown_elements": sum(1 for e in els if e["status"] == "unknown"),
        "rules": [{"rule_id": r.rule_id, "title_ko": r.title_ko, "status": r.status, "status_ko": d["results"][i]["status_ko"],
                   "message_ko": r.message_ko} for i, r in enumerate(report.results)],
        "units_scale": model.get("units_scale"),
    }


_KIND_KO = {"added": "추가", "removed": "삭제", "modified": "수정"}


def _group_blocks(changes: list[dict[str, Any]], block_of: dict[str, str | None]) -> list[dict[str, Any]]:
    """펼친 블록의 하위 객체 변경을 블록·종류마다 한 항목으로 묶는다(실도면 크레인 블록 하나를 옮기면 하위 객체 수천 개가 '수정').
    묶은 항목: element_id = 블록 handle, members = 하위 handle 목록, bbox = 합친 범위, 해결/새 위반/확인 필요는 규칙별 하나로."""
    out: list[dict[str, Any]] = []
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for c in changes:
        b = block_of.get(c["element_id"]) if c.get("element_id") else None
        if not b:
            out.append(c)
            continue
        g = groups.get((b, c["kind"]))
        if g is None:
            g = {**c, "element_id": b, "members": [], "resolved": [], "introduced": [], "needs_check": []}
            groups[(b, c["kind"])] = g
            out.append(g)
        g["members"].append(c["element_id"])
        for key in ("resolved", "introduced", "needs_check"):
            for x in c[key]:
                if all(y["rule_id"] != x["rule_id"] for y in g[key]):
                    g[key].append(x)
        if c["bbox"]:
            g["bbox"] = c["bbox"] if not g.get("bbox") else [min(g["bbox"][0], c["bbox"][0]), min(g["bbox"][1], c["bbox"][1]),
                                                             max(g["bbox"][2], c["bbox"][2]), max(g["bbox"][3], c["bbox"][3])]
    for (b, kind), g in groups.items():
        n = len(g["members"])
        if n == 1:
            continue
        bb = g["bbox"]
        if bb:
            g["center"] = [(bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2]
            span = f"x {bb[0]:.1f}~{bb[2]:.1f} m, 상단 z {bb[3]:.2f} m"
        else:
            span = "좌표 없음"
        g["desc_ko"] = f"{g['role_ko']} 블록 {b} {_KIND_KO.get(kind, kind)} (하위 객체 {n}개)"
        g["before_ko"] = "없음" if kind == "added" else f"블록 {b} — 변경 범위 {span}"
        g["after_ko"] = "없음" if kind == "removed" else f"블록 {b} — 변경 범위 {span}"
    for g in groups.values():
        if len(g["members"]) == 1:
            g["element_id"] = g["members"][0]       # 하위 객체 하나만 바뀌었으면 원래 항목 그대로
            g.pop("members")
    return out


def compare_models(ma: dict[str, Any], mb: dict[str, Any], labels: tuple[str, str] = ("A", "B")) -> dict[str, Any]:
    rules = all_rules()
    ra, rb = evaluate(ma, rules), evaluate(mb, rules)
    diff = diff_models(ma, mb)
    oa = {o["handle"]: o for o in ma["objects"]}
    ob = {o["handle"]: o for o in mb["objects"]}
    ea, eb = _elements_by_handle(ra), _elements_by_handle(rb)
    rules_by_role = {}
    for rule in rules:
        for role in rule.roles:
            rules_by_role.setdefault(role, []).append(rule)
    rule_status_a = {r.rule_id: r.status for r in ra.results}
    rule_status_b = {r.rule_id: r.status for r in rb.results}
    title = {r.id: r.title_ko for r in rules}

    def ref(rule_id: str, text: str) -> dict[str, str]:
        return {"rule_id": rule_id, "rule_name": title[rule_id], "text": text}

    def elem_transitions(handle: str, removed: bool = False) -> tuple[list[dict], list[dict], list[dict]]:
        """객체 하나의 규칙별 상태 변화. '해결'은 이후가 충족이거나 객체가 삭제된 경우만 —
        판정을 못 하게 된 것(unknown)이나 판정 대상에서 빠진 것(규칙 적용 제외)은 '확인 필요'로 따로 낸다."""
        res, intro, check = [], [], []
        for rule in rules:
            sa = _status_of(ea.get(handle), rule.id)
            sb = None if removed else _status_of(eb.get(handle), rule.id)
            if sa == "fail" and removed:
                # 삭제로 해결 — 단, 삭제 뒤 그 규칙을 판정할 수 있을 때만(입력 누락·검토 불가·적용 제외면 '확인 필요')
                if rule_status_b.get(rule.id) in ("pass", "fail"):
                    res.append(ref(rule.id, f"{rule.title_ko} 위반 해결 (객체 삭제로)"))
                else:
                    check.append(ref(rule.id, f"{rule.title_ko} 위반 객체가 삭제됐지만 규칙을 판정할 수 없음 — 확인 필요"))
            elif sa == "fail" and sb == "pass":
                res.append(ref(rule.id, f"{rule.title_ko} 위반 해결 (기준 충족으로)"))
            elif sb == "fail" and sa != "fail":
                intro.append(ref(rule.id, f"{rule.title_ko} 새 위반"))
            elif not removed and sa != sb and sb in ("unknown", None) and sa is not None:
                why = "판정 못 함으로 바뀜" if sb == "unknown" else "판정 대상에서 빠짐(적용 제외 등)"
                check.append(ref(rule.id, f"{rule.title_ko} {why} — 확인 필요"))
        return res, intro, check

    def rule_transitions(rule_ids: list[str]) -> tuple[list[dict], list[dict], list[dict]]:
        res, intro, check = [], [], []
        for rid in rule_ids:
            sa, sb = rule_status_a.get(rid), rule_status_b.get(rid)
            if sa == sb:
                continue
            if sa == "fail" and sb == "pass":
                res.append(ref(rid, f"{title[rid]} 위반 해결"))
            elif sb == "fail":
                intro.append(ref(rid, f"{title[rid]} 새 위반"))
            elif sb in ("na", "review"):
                check.append(ref(rid, f"{title[rid]} {'입력 누락으로' if sb == 'na' else '검토 불가로'} 바뀜 — 확인 필요"))
            elif sb == "skip":
                check.append(ref(rid, f"{title[rid]} 적용 제외로 바뀜 — 적용 조건(LOA·용골 거치일 등) 입력 확인"))
        return res, intro, check

    changes: list[dict[str, Any]] = []

    def geo_change(kind: str, handle: str) -> None:
        a_obj, b_obj = oa.get(handle), ob.get(handle)
        role = (b_obj or a_obj)["role"]
        sa, sb = _stats(a_obj), _stats(b_obj)
        if kind == "added":
            desc = f"{ROLE_KO.get(role, '객체')} 추가 — {_shape_ko(role, sb)}"
            res, intro, check = elem_transitions(handle)
        elif kind == "removed":
            desc = f"{ROLE_KO.get(role, '객체')} 삭제 — {_shape_ko(role, sa)}"
            res, intro, check = elem_transitions(handle, removed=True)
        else:
            desc = _modified_desc(role, sa, sb, ra, rb)
            res, intro, check = elem_transitions(handle)
        boxes = [s for s in (sa, sb) if s]
        bbox = [min(s["x0"] for s in boxes), min(s["z0"] for s in boxes),
                max(s["x1"] for s in boxes), max(s["z1"] for s in boxes)] if boxes else None
        at = sb or sa
        center = [(at["x0"] + at["x1"]) / 2, (at["z0"] + at["z1"]) / 2] if at else None
        changes.append({
            "kind": kind, "element_id": handle, "role": role, "role_ko": ROLE_KO.get(role, role),
            "desc_ko": desc,
            "before_ko": "없음" if kind == "added" else _shape_ko(role, sa),
            "after_ko": "없음" if kind == "removed" else _shape_ko(role, sb),
            "resolved": res, "introduced": intro, "needs_check": check,
            "related_rules": [r.id for r in rules_by_role.get(role, [])],
            "center": center, "bbox": bbox,
        })

    for h in diff["modified"]:
        geo_change("modified", h)
    for h in [] if diff["initial"] else diff["added"]:
        geo_change("added", h)
    for h in diff["removed"]:
        geo_change("removed", h)

    # 선박 정보·흘수 조건 변경의 규칙 수준 변화는 규칙마다 한 번만(여러 값이 함께 바뀌어도 중복해 붙이지 않음)
    attributed: set[str] = set()

    def rule_transitions_once(rule_ids: list[str]):
        fresh = [r for r in rule_ids if r not in attributed]
        attributed.update(fresh)
        return rule_transitions(fresh)

    # 선박 정보 값끼리 대조하는 검사(L_LL↔LOA, AP↔FP 등)는 규칙이 직접 읽지 않는 값으로도 판정을 막는다
    # → 그런 선박 정보 문제(issue)가 생기거나 없어진 규칙도 선박 정보 변경과 관련된 규칙으로 본다.
    SHIP_ISSUES = {"SHIP_VALUE_INVALID", "SHIP_VALUE_MISMATCH", "PERP_ORDER", "PROP_INVALID", "PROP_DUPLICATE"}

    def ship_issue_msgs(model: dict[str, Any], rule) -> set[str]:
        return {i["message_ko"] for i in issues_for(model, rule.roles) if i["code"] in SHIP_ISSUES}

    ship_issue_changed = {r.id for r in rules if ship_issue_msgs(ma, r) != ship_issue_msgs(mb, r)}

    def with_cause(refs: list[dict]) -> list[dict]:
        """선박 정보가 여러 개 함께 바뀌었으면 어느 값 때문인지 단정하지 않는다."""
        if len(diff["ship_changed"]) <= 1:
            return refs
        keys = "·".join(SHIP_KO.get(kk, kk) for kk in diff["ship_changed"])
        return [{**x, "text": x["text"] + f" (함께 바뀐 {keys} 중 원인 확인)"} for x in refs]

    for k in diff["ship_changed"]:
        va, vb = ma["ship"].get(k), mb["ship"].get(k)
        related = [r.id for r in rules if k in r.ship_keys or r.id in ship_issue_changed]
        res, intro, check = (with_cause(x) for x in rule_transitions_once(related))
        unit = "" if k in ("keel_laid", "ship_type") else " m"
        fa = "없음" if va is None else f"{va:g}{unit}" if isinstance(va, (int, float)) else str(va)
        fb = "없음" if vb is None else f"{vb:g}{unit}" if isinstance(vb, (int, float)) else str(vb)
        changes.append({"kind": "ship", "element_id": None, "role": None, "role_ko": "선박 정보",
                        "desc_ko": f"선박 정보 {SHIP_KO.get(k, k)} {fa} → {fb}", "before_ko": fa, "after_ko": fb,
                        "resolved": res, "introduced": intro, "needs_check": check, "related_rules": related,
                        "center": None, "bbox": None})
    if not diff["initial"] and diff["conditions_changed"]:
        related = [r.id for r in rules if r.uses_conditions]
        res, intro, check = rule_transitions_once(related)
        fa, fb = _fmt_conditions(ma["conditions"]), _fmt_conditions(mb["conditions"])
        changes.append({"kind": "conditions", "element_id": None, "role": None, "role_ko": "흘수 조건",
                        "desc_ko": f"흘수 조건 {fa} → {fb}", "before_ko": fa, "after_ko": fb,
                        "resolved": res, "introduced": intro, "needs_check": check, "related_rules": related,
                        "center": None, "bbox": None})

    changes = _group_blocks(changes, {o["handle"]: o.get("block") for o in ma["objects"] + mb["objects"]})
    changes.sort(key=lambda c: (_KIND_ORDER[c["kind"]], c["center"][0] if c["center"] else math.inf,
                                c["element_id"] or c["desc_ko"]))
    for i, c in enumerate(changes, 1):
        c["number"] = i

    # 바뀌지 않은 객체인데 판정이 바뀐 것 (예: 선교를 올려서 앞 장애물이 해결) — 특정 변경 하나에 붙이지 않고 따로 알린다
    changed = {h for c in changes for h in (c.get("members") or [c["element_id"]]) if h}
    indirect = []
    lost: dict[tuple[str, str | None], list[str]] = {}   # (규칙, 이후 상태) → 판정 못 하게 된 객체들
    for h in sorted(set(oa) & set(ob) - changed):
        for rule in rules:
            sa, sb = _status_of(ea.get(h), rule.id), _status_of(eb.get(h), rule.id)
            if sa == sb or not ({"fail", "unknown", None} & {sa, sb}):
                continue
            if sb in ("unknown", None):
                lost.setdefault((rule.id, sb), []).append(h)
                continue
            what = "위반 해결" if sa == "fail" and sb == "pass" else "새 위반" if sb == "fail" else "판정 가능해짐"
            indirect.append({"element_id": h, "element_ids": [h], "count": 1, "role_ko": ROLE_KO.get(ob[h]["role"], ob[h]["role"]),
                             "rule_id": rule.id, "rule_name": rule.title_ko, "from": sa, "to": sb,
                             "text": f"{ROLE_KO.get(ob[h]['role'], '객체')}(handle {h}) {rule.title_ko}: {what} (다른 변경의 영향)"})
    # 판정 못 하게 된 것은 규칙마다 한 줄로 묶는다(LOA 오입력 하나로 객체 수백 줄이 나오지 않게)
    for (rid, sb), hs in sorted(lost.items(), key=lambda kv: (kv[0][0], str(kv[0][1]))):
        why = "판정 못 함으로 바뀜" if sb == "unknown" else "판정 대상에서 빠짐"
        froms = sorted({_status_of(ea.get(h), rid) for h in hs}, key=str)   # 하나로 같으면 그 값(없던 상태면 null), 섞이면 null
        if len(hs) == 1:
            h = hs[0]
            who = f"{ROLE_KO.get(ob[h]['role'], '객체')}(handle {h})"
        else:
            kinds: dict[str, int] = {}
            for h in hs:
                k = ROLE_KO.get(ob[h]["role"], "객체")
                kinds[k] = kinds.get(k, 0) + 1
            who = f"객체 {len(hs)}개(" + "·".join(f"{k} {n}" for k, n in kinds.items()) + ")"
        indirect.append({"element_id": hs[0], "element_ids": hs, "count": len(hs),
                         "role_ko": ROLE_KO.get(ob[hs[0]]["role"], ob[hs[0]]["role"]),
                         "rule_id": rid, "rule_name": title[rid], "from": froms[0] if len(froms) == 1 else None, "to": sb,
                         "text": f"{who} {title[rid]}: {why} — 확인 필요 (다른 변경의 영향)"})

    resolved_rules, introduced_rules, needs_check_rules = rule_transitions([r.id for r in rules])
    fail_a = sum(1 for els in ea.values() for e in els if e["status"] == "fail")
    fail_b = sum(1 for els in eb.values() for e in els if e["status"] == "fail")

    def geometry(model: dict[str, Any], els: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
        out = []
        for o in model["objects"]:
            sts = [e["status"] for e in els.get(o["handle"], [])]
            worst = "fail" if "fail" in sts else "unknown" if "unknown" in sts else "pass" if sts else None
            out.append({"handle": o["handle"], "role": o["role"], "points": o["points"],
                        "closed": bool(o.get("closed")), "status": worst})
        return out

    all_pts = [p for m in (ma, mb) for o in m["objects"] for p in o["points"]]
    bounds = ([min(p[0] for p in all_pts), min(p[1] for p in all_pts), max(p[0] for p in all_pts), max(p[1] for p in all_pts)]
              if all_pts else None)
    return {
        "schema": "garc.compare/1",
        "a": _version_summary(labels[0], ma, ra),
        "b": _version_summary(labels[1], mb, rb),
        "changes": changes,
        "resolved_rules": resolved_rules,
        "introduced_rules": introduced_rules,
        "needs_check_rules": needs_check_rules,
        "indirect": indirect,
        "violations": {"before": fail_a, "after": fail_b},
        "unknown": {"before": sum(1 for els in ea.values() for e in els if e["status"] == "unknown"),
                    "after": sum(1 for els in eb.values() for e in els if e["status"] == "unknown")},
        "geometry": {"a": geometry(ma, ea), "b": geometry(mb, eb)},
        "elements": {"a": [e for els in ea.values() for e in els], "b": [e for els in eb.values() for e in els]},
        "bounds": bounds,
        "units_scale": mb.get("units_scale") or ma.get("units_scale"),
        # 형상·특성 변경이 없어도 판정이 바뀌었으면(유사 레이어 객체 추가 등) 같은 상태가 아니다
        "same": not changes and rule_status_a == rule_status_b
                and {h: sorted((e["rule_id"], e["status"]) for e in v) for h, v in ea.items()}
                == {h: sorted((e["rule_id"], e["status"]) for e in v) for h, v in eb.items()},
    }


def compare_raws(raw_a: dict[str, Any], raw_b: dict[str, Any], labels: tuple[str, str] = ("A", "B")) -> dict[str, Any]:
    return compare_models(raw_to_model(raw_a), raw_to_model(raw_b), labels)
