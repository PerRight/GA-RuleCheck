"""명령행.

  python -m garc serve [--port 8765]          AutoCAD 플러그인용 로컬 엔진 서버
  python -m garc check <file.dxf|model.json> [--roles map.json] [--prop KEY=값 …]  한 도면 판정(+ 준비 상태)
  python -m garc particulars <file.dxf>        주요 치수표·축척 후보(실도면 연결)
  python -m garc revs <rev0.dxf> <rev1.dxf> … 순서대로 넣어 revision diff·상태 변화 확인
  python -m garc report <file…> [-o out.html]  검토 리포트(HTML) — 여러 파일이면 revision 이력 포함
  python -m garc compare <A> <B> [--json]      두 버전 비교 — 변경 항목·해결된 위반 (플러그인 비교 창과 같은 계산)
  python -m garc rules                         규칙 목록
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .contract import raw_to_model, validate_model
from .engine import all_rules, evaluate, OVERALL_KO, STATUS_KO, DISCLAIMER_KO
from .session import Session


def load_any(path: str, roles: dict[str, str] | None = None, props: dict[str, str] | None = None) -> dict[str, Any]:
    """DXF·raw JSON·model JSON. roles = {handle: 역할}(시험용 역할 지정), props = 도면 특성 덮어쓰기."""
    p = Path(path)
    if p.suffix.lower() == ".dxf":
        from .adapters.dxf import read_dxf
        raw = read_dxf(p, roles=roles)
        raw["props"].update(props or {})
        return raw_to_model(raw)
    data = json.loads(p.read_text(encoding="utf-8"))
    if data.get("schema", "").startswith("garc.raw"):
        data.setdefault("props", {}).update(props or {})
        return raw_to_model(data)
    validate_model(data)
    return data


def _print_report(rep: dict[str, Any]) -> None:
    for r in rep["results"]:
        print(f"[{r['status'].upper():6}] {r['rule_id']:16} {r['title_ko']}")
        print(f"         {r['message_ko']}")
        if r.get("suggestion_ko"):
            print(f"         제안: {r['suggestion_ko']}")
        if r.get("targets"):
            print(f"         대상 handle: {', '.join(r['targets'])}")
    print(f"-- 전체: {rep['overall'].upper()} — {rep['overall_ko']}")
    print(f"   ※ {DISCLAIMER_KO}")


def main(argv: list[str] | None = None) -> int:
    # cp949 콘솔·파이프에서 '—'·'→'·'⚠' 같은 문자로 죽지 않게 — 인코딩은 그대로 두고 못 쓰는 문자만 '?'로.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(prog="garc")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve"); s.add_argument("--port", type=int, default=8765)
    c = sub.add_parser("check"); c.add_argument("path"); c.add_argument("--json", action="store_true")
    c.add_argument("--roles", help="시험용 역할 지정 JSON {handle: 역할}")
    c.add_argument("--prop", action="append", default=[], help="도면 특성 덮어쓰기 KEY=값 (여러 번)")
    pt = sub.add_parser("particulars"); pt.add_argument("path"); pt.add_argument("--json", action="store_true")
    v = sub.add_parser("revs"); v.add_argument("paths", nargs="+")
    rp = sub.add_parser("report"); rp.add_argument("paths", nargs="+"); rp.add_argument("-o", "--out", default="ga_report.html")
    cp = sub.add_parser("compare"); cp.add_argument("a"); cp.add_argument("b"); cp.add_argument("--json", action="store_true")
    sub.add_parser("rules")
    a = ap.parse_args(argv)

    if a.cmd == "serve":
        from .server import serve
        serve(a.port)
    elif a.cmd == "check":
        roles = json.loads(Path(a.roles).read_text(encoding="utf-8")) if a.roles else None
        props = dict(kv.split("=", 1) for kv in a.prop if "=" in kv)
        model = load_any(a.path, roles=roles, props=props)
        rep = evaluate(model).to_dict()
        from .readiness import readiness
        rep["readiness"] = ready = readiness(model)
        if a.json:
            print(json.dumps(rep, ensure_ascii=False, indent=2, default=str))
        else:
            _print_report(rep)
            if not ready["all_ready"]:
                print(f"-- 준비 상태 {ready['ready']}/{ready['total']}")
                mark = {"ok": "✔", "default": "·", "missing": "✖", "invalid": "⚠", "optional": "○"}
                for it in ready["items"]:
                    if it["status"] in ("missing", "invalid") or (it["status"] == "default" and it["required"]):
                        print(f"   {mark[it['status']]} {it['name_ko']}: {it['problem_ko'] or it['value_ko'] or '없음'}"
                              + (f" — {it['hint_ko']}" if it["hint_ko"] else ""))
                for msg in ready["problems_ko"]:
                    print(f"   ⚠ {msg}")
        return {"pass": 0, "fail": 1}.get(rep["overall"], 2)
    elif a.cmd == "particulars":
        from .adapters.dxf import read_texts
        from .particulars import find_particulars
        out = find_particulars(read_texts(a.path))
        if a.json:
            print(json.dumps(out, ensure_ascii=False, indent=2))
        else:
            for k, v in out["items"].items():
                val = v["value"] if v["status"] == "found" else ("값이 여러 개 — " + ", ".join(f"{c['value']:g}" for c in v["candidates"])
                                                                if v["status"] == "conflict" else "못 찾음")
                print(f"  {v['name_ko']:14} {val}")
            print("  도면 특성 제안:", "; ".join(f"{k}={v}" for k, v in out["suggested_props"].items()) or "없음")
            for c in out["suggested_conditions"]:
                print(f"  흘수 조건 후보: {c['name']}:{c['t_aft']:g}/{c['t_fwd']:g}")
            for h in out["human_inputs_ko"]:
                print(f"  ※ 사람 입력: {h}")
    elif a.cmd == "revs":
        sess = Session()
        for p in a.paths:
            out = sess.check(load_any(p))
            d = out["diff"]
            print(f"=== rev {out['revision']}: {Path(p).name}"
                  + (f"  (rev {out['same_as_revision']}와 동일 — Undo 상태)" if out["same_as_revision"] else ""))
            if not d["initial"]:
                print(f"    변경: 추가 {d['added']} 삭제 {d['removed']} 수정 {d['modified']}"
                      f" 선박정보 {d['ship_changed']} 흘수조건 {'변경' if d['conditions_changed'] else '-'}")
            print(f"    영향 규칙: {out['affected_rules']}  재계산: {out['rerun_rules']}  캐시: {out['cached_rules']}")
            for t in out["transitions"]:
                print(f"    ⚠ {t['rule_id']}: {STATUS_KO.get(t['from'], t['from'])} → {STATUS_KO[t['to']]} ({t['kind']})")
            for r in out["results"]:
                print(f"    [{r['status'].upper():6}] {r['rule_id']:16} {r['message_ko']}")
            print(f"    전체: {OVERALL_KO[out['overall']]}")
    elif a.cmd == "report":
        from .report import build_from_session
        sess = Session()
        for p in a.paths:
            sess.check(load_any(p))
        Path(a.out).write_text(build_from_session(sess, Path(a.paths[-1]).name), encoding="utf-8")
        print(f"리포트 작성: {a.out}")
    elif a.cmd == "compare":
        from .compare import compare_models
        out = compare_models(load_any(a.a), load_any(a.b), (Path(a.a).stem, Path(a.b).stem))
        if a.json:
            print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
            return 0
        va, vb = out["a"], out["b"]
        print(f"=== {va['label']} → {vb['label']}")
        print(f"    전체: {OVERALL_KO[va['overall']]} → {OVERALL_KO[vb['overall']]}")
        print(f"    미충족 요소: {out['violations']['before']} → {out['violations']['after']}   "
              f"확인 필요 요소: {out['unknown']['before']} → {out['unknown']['after']}")
        for x in out["needs_check_rules"]:
            print(f"    ⚠ {x['text']}")
        for c in out["changes"]:
            print(f"  {c['number']:>2}. [{c['kind']}] {c['desc_ko']}")
            for x in c["resolved"]:
                print(f"      ✔ {x['text']}")
            for x in c["introduced"]:
                print(f"      ✖ {x['text']}")
            for x in c["needs_check"]:
                print(f"      ⚠ {x['text']}")
        for x in out["indirect"]:
            print(f"   ※ {x['text']}")
        if out["same"]:
            print("    변경 없음")
        elif not out["changes"]:
            print("    GA_ 객체·선박 정보 변경은 없지만 판정이 바뀜 (위 ⚠ 참고 — 유사 이름 레이어 등)")
    elif a.cmd == "rules":
        for r in all_rules():
            print(f"{r.id:16} {r.title_ko}  ({r.ref}; {r.edition})")
            for lim in r.limitations:
                print(f"    - 미검토: {lim}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
