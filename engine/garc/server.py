"""로컬 HTTP 엔진 서버 (표준 라이브러리만). AutoCAD 플러그인이 호출한다.

  GET  /v1/health                          → {"ok": true, "version", "rules": [...]}
  POST /v1/check  {"doc_id", "raw"|"model"} → 판정 + revision diff
  POST /v1/reset  {"doc_id"}               → 해당 문서 세션 초기화
  POST /v1/report {"doc_id", "title"?}     → {"html": 검토 리포트} (마지막 revision + 이력)
  POST /v1/compare {"a": raw, "b": raw, "labels"?: ["v1","v2"]} → 버전 비교(변경 항목·해결된 위반·형상)
  POST /v1/particulars {"texts": [{"text", "x", "y", "h"}]} → 주요 치수표·축척 후보(실도면 연결)

127.0.0.1에만 바인딩한다(도면 데이터가 PC 밖으로 나가지 않음).
추가로 Host 헤더가 루프백 이름이 아니거나 브라우저 Origin 헤더가 붙은 요청은 거부한다
(웹 페이지가 DNS rebinding·교차 사이트 요청으로 로컬 엔진에 접근하는 경로 차단. 플러그인·urllib은 Origin을 보내지 않는다).
"""
from __future__ import annotations

import json
import re
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from . import __version__
from .contract import raw_to_model, ContractError
from .engine import all_rules
from .session import Session

_sessions: dict[str, Session] = {}
_lock = threading.Lock()
_LOOPBACK_HOST = re.compile(r"(127\.0\.0\.1|localhost|\[::1\])(:\d{1,5})?")


def _request_allowed(headers) -> bool:
    """루프백 Host(127.0.0.1·localhost·[::1], 포트 선택) + Origin 없음일 때만 허용. Host가 여러 개면 거부."""
    if headers.get("Origin") is not None:
        return False
    hosts = headers.get_all("Host") or []
    if len(hosts) != 1:
        return False
    return _LOOPBACK_HOST.fullmatch(hosts[0].strip().lower()) is not None


def _session(doc_id: str) -> Session:
    with _lock:
        if doc_id not in _sessions:
            _sessions[doc_id] = Session()
        return _sessions[doc_id]


def handle_check(body: dict[str, Any]) -> dict[str, Any]:
    doc_id = str(body.get("doc_id") or "default")
    if "raw" in body:
        model = raw_to_model(body["raw"])
    elif "model" in body:
        model = body["model"]
    else:
        raise ContractError("raw 또는 model이 필요합니다.")
    out = _session(doc_id).check(model)
    out["doc_id"] = doc_id
    return out


def handle_compare(body: dict[str, Any]) -> dict[str, Any]:
    from .compare import compare_raws
    if not isinstance(body.get("a"), dict) or not isinstance(body.get("b"), dict):
        raise ContractError("비교할 두 스냅샷 a, b(raw)가 필요합니다.")
    labels = body.get("labels") or ["A", "B"]
    if not (isinstance(labels, list) and len(labels) == 2):
        raise ContractError("labels는 [이전, 이후] 두 개여야 합니다.")
    return compare_raws(body["a"], body["b"], (str(labels[0]), str(labels[1])))


def handle_particulars(body: dict[str, Any]) -> dict[str, Any]:
    from .particulars import find_particulars
    texts = body.get("texts")
    if not isinstance(texts, list):
        raise ContractError("texts(문자 목록)가 필요합니다.")
    if len(texts) > 50_000:
        raise ContractError("문자가 너무 많습니다(5만 개 초과).")
    return find_particulars(texts)


class Handler(BaseHTTPRequestHandler):
    server_version = "garc/" + __version__

    def _send(self, code: int, payload: dict[str, Any]) -> None:
        data = encode_payload(payload)
        if data is None:   # NaN·무한대가 섞인 결과 — C# 파서가 못 읽고, '통과'로 보일 여지도 없애기 위해 오류로 보낸다
            code, data = 500, encode_payload({"error": "엔진 결과에 유한하지 않은 값이 있습니다 — 선박 정보·좌표 입력을 확인하세요."})
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _forbidden(self) -> bool:
        if _request_allowed(self.headers):
            return False
        self._send(403, {"error": "로컬 플러그인 요청만 받습니다 (Host가 루프백이 아니거나 Origin 헤더가 있음)."})
        return True

    def do_GET(self):  # noqa: N802
        if self._forbidden():
            return
        if self.path == "/v1/health":
            self._send(200, {"ok": True, "version": __version__,
                             "rules": [{"id": r.id, "ref": r.ref, "edition": r.edition} for r in all_rules()]})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):  # noqa: N802
        if self._forbidden():
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
            if self.path == "/v1/check":
                self._send(200, handle_check(body))
            elif self.path == "/v1/report":
                from .report import build_from_session
                doc_id = str(body.get("doc_id") or "default")
                with _lock:
                    sess = _sessions.get(doc_id)
                if sess is None:
                    raise ValueError("이 도면은 아직 검사하지 않았습니다. 먼저 검사하세요.")
                self._send(200, {"html": build_from_session(sess, body.get("title"))})
            elif self.path == "/v1/compare":
                self._send(200, handle_compare(body))
            elif self.path == "/v1/particulars":
                self._send(200, handle_particulars(body))
            elif self.path == "/v1/reset":
                with _lock:
                    _sessions.pop(str(body.get("doc_id") or "default"), None)
                self._send(200, {"ok": True})
            else:
                self._send(404, {"error": "not found"})
        except (ContractError, ValueError) as e:
            self._send(400, {"error": str(e)})
        except Exception as e:  # 엔진 오류도 '통과'로 보이면 안 된다 → 플러그인은 검토 불가로 표시
            traceback.print_exc()
            self._send(500, {"error": f"엔진 내부 오류: {e}"})

    def log_message(self, fmt, *args):  # 조용히
        pass


def encode_payload(payload: dict[str, Any]) -> bytes | None:
    """JSON 인코딩. NaN·Infinity는 표준 JSON이 아니므로(allow_nan=False) 인코딩하지 않고 None."""
    try:
        return json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except ValueError:
        return None


def serve(port: int = 8765) -> None:
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"GA-RuleCheck 엔진 http://127.0.0.1:{port}  (규칙 {len(all_rules())}개)  Ctrl+C로 종료")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
