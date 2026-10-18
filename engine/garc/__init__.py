"""GA-RuleCheck 규칙 엔진 (작업명).

CAD를 모르는 순수 파이썬 패키지. 표준 라이브러리만 쓴다(DXF 어댑터만 ezdxf 선택 의존).
AutoCAD 플러그인·DXF·테스트는 모두 같은 입력 계약(contract.py)을 거쳐 이 엔진을 호출한다.
"""
from .contract import raw_to_model, validate_model, ContractError
from .engine import evaluate, Report, RuleResult, DISCLAIMER_KO
from .session import Session, diff_models

__all__ = [
    "raw_to_model", "validate_model", "ContractError",
    "evaluate", "Report", "RuleResult", "DISCLAIMER_KO",
    "Session", "diff_models",
]
__version__ = "0.4.0"  # 0.2.0: raw bulges/closed, 입력 값·형상 검증 / 0.2.1: 오타 감지(눈 위치·흘수·기선·용골일·숫자 형식·유사 레이어) (2026-10-12) / 0.3.0: 요소별 판정·버전 비교 /v1/compare / 0.4.0: 실도면 연결(축척·영역·역할·블록 펼침·준비 상태·치수표 읽기) (2026-10-18)
