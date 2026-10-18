# GA-RuleCheck (작업명)

**GA 도면이 바뀌면, 관련 규정만 다시 검사해 도면 위에 경고와 수치 제안을 띄우는 AutoCAD 플러그인**
2026 K-조선 해커톤 · 홍대도면반

> ⚠ 규칙 엔진의 **후보 판정**입니다. 선급 승인이나 적합 증명이 아니며, 최종 판단은 설계자·선급 검토로 합니다.

---

## 무엇을 하나
1. 설계자가 AutoCAD에서 GA(일반배치도) 측면도를 고친다 — 예: 선교 앞 갑판에 컨테이너를 한 단 더 쌓는다.
2. 플러그인이 `GA_*` 레이어의 변경을 감지해 도면 스냅샷을 로컬 규칙 엔진(`127.0.0.1`)에 보낸다.
3. 엔진이 **바뀐 입력에 연결된 규칙만** 다시 판정한다(나머지는 캐시, Undo도 즉시 복귀).
4. 도면 위에 위반 객체·시선·허용선과 함께 "x=112 m 장애물 상단을 z=22.45 m 이하로" 같은 **계산된 제안**을 띄운다.
5. 도면을 저장하면 버전(작성자·수정 사유·관련 규정)을 남기고, **이전 버전과 무엇이 달라졌고 어떤 위반이 해결됐는지**를 비교 창과 생산팀 전달용 PDF/PNG 한 장으로 보여 준다.
6. GA_ 레이어로 정리되지 않은 **실제 도면**도 레이어를 바꾸지 않고 연결한다(`GARCLINK`) — 도면 문자에서 치수·축척을 읽어 확인받고, 측면도 영역·기준점·객체 역할만 지정하면 검사된다.

### 주요 화면 (플러그인 v0.5)
- **실도면 연결**: 치수표·축척 후보 확인 창 → 측면도 영역 → AP·FP·기선 → 선체·장애물·충돌격벽 역할 지정(도면 사전에 저장, 레이어 그대로) → 첫 검사. 팔레트에 **준비 상태 n/10**(빠진 입력 한글 목록), `GARCROLESHOW`로 판정에 쓰인 객체를 역할별 색으로
- **버전 비교**: 나란히(같이 확대·이동) / 겹쳐(투명도) / 차이만(추가 초록 빗금 · 삭제 빨강 파선 · 변경 노랑), 번호 표식과 변경 내역(변경 전·후 값, 사유, 해결된 위반), **생산팀 전달** 내보내기(흑백 인쇄에서도 구분)
- **떠 있는 로고**: 오른쪽 아래 로고를 누르거나 `Ctrl+.` → 검토 패널이 펼쳐지고 접힘, 미충족 개수 배지
- **마우스 오버**: 객체에 커서를 올리면 충족(파랑) · 미충족(빨강) · 확인 필요(주황)로 강조하고 기준·실제·차이·수정 가이드를 툴팁으로, 패널 점검 항목과 도면이 서로 연동

AI가 설계하는 도구가 아니라 **변경 이후 검토를 보조**하는 도구입니다. 판정은 결정론적 규칙 엔진만 하고, 입력이 모자라거나 모호하면 추측하지 않고 **'검토 불가'로 멈춥니다**(충족으로 보이지 않음).

## 검사 규칙 (MVP)
| ID | 근거 | 기준 | 길이 L |
|---|---|---|---|
| `SOLAS-V-22.1.1` | SOLAS V/22.1.1 선교 가시거리 | 선수 전방 수면 사각 거리 ≤ min(2L, 500 m), 입력된 모든 흘수 조건 중 최악 | 전장 LOA |
| `SOLAS-II1-12` | SOLAS II-1/12 충돌격벽 위치 | min(0.05L, 10 m) ≤ 기준점~격벽 ≤ max(0.08L, 0.05L+3 m), 구상선수 기준점·계단형 | 만재흘수선협약 L |

정의·원문·다루지 않는 범위: [docs/rule-definitions.md](docs/rule-definitions.md) · 판본 대조는 진행 중

## 구조
```
AutoCAD 2027 ── autocad/GaRuleCheck (C# .NET 10, 판단하지 않는 어댑터)
                  변경 감지 → GA_ 레이어 스냅샷 추출 → 엔진 호출 → 팔레트·리본·도면 위 임시 표시
                     │ POST http://127.0.0.1:8765/v1/check
                     ▼
engine/garc (Python 3.10+, 표준 라이브러리만)
  contract(입력 계약) → rules(규칙 2개) → engine(판정) → session(변경 diff·캐시) → server / report(HTML)
  DXF 경로(adapters/dxf.py, ezdxf)로 AutoCAD 없이도 같은 흐름을 재현
```

## 빠른 시작 1 — 엔진만 (AutoCAD 없이)
```bash
cd engine
pip install ezdxf                       # DXF 경로에만 필요
python -m unittest discover -s tests    # 24건 기대값 포함 140개 테스트 (ezdxf 없으면 DXF 테스트는 skip)
python -m garc revs ../samples/S140_rev*.dxf
```
출력 예:
```
=== rev 2: S140_rev1_deck_containers.dxf
    변경: 추가 ['9A'] …
    영향 규칙: ['SOLAS-V-22.1.1']  재계산: ['SOLAS-V-22.1.1']  캐시: ['SOLAS-II1-12']
    ⚠ SOLAS-V-22.1.1: 기준 충족 → 위반 (new_violation)
    [FAIL  ] SOLAS-V-22.1.1   사각 거리 433.84 m > 허용 280 m (최악: 밸러스트 도착)
=== rev 4: S140_rev3_undo.dxf  (rev 2와 동일 — Undo 상태)
    영향 규칙: ['SOLAS-II1-12']  재계산: []  캐시: ['SOLAS-V-22.1.1', 'SOLAS-II1-12']
```
그 밖에: `python -m garc check <도면.dxf>` (한 도면 판정), `python -m garc report <도면…> -o 리포트.html` (HTML 검토 리포트), `python -m garc serve` (플러그인용 로컬 서버)

## 빠른 시작 2 — AutoCAD 2027 플러그인
준비물: AutoCAD 2027(LT 제외), .NET 10 SDK, Python 3.10+
```powershell
# AutoCAD를 모두 닫고
cd autocad
powershell -ExecutionPolicy Bypass -File .\install.ps1   # 빌드 → 자동 로드 번들 설치 → 엔진 자동 실행 설정
```
AutoCAD에서 `samples\S140_rev0_base.dxf`를 열고 `GARC` → 오른쪽 팔레트에 결과. 명령·화면·첫 실행 확인표: [autocad/README.md](autocad/README.md)

## 데모 시나리오 (합성 GA, S140 일반화물선 LOA 140 m)
| 파일 | 변경 | 결과 |
|---|---|---|
| `S140_rev0_base.dxf` | 기본 | 두 규칙 모두 기준 충족 |
| `S140_rev1_deck_containers.dxf` | 선교 앞 컨테이너 4단(상단 z=24 m) | 가시거리 위반 433.84 m > 280 m, 제안 "z=22.45 m 이하로" |
| `S140_rev2_bulkhead_moved.dxf` | 충돌격벽 2 m 선수 쪽 이동 | 충돌격벽 상한 초과 |
| `S140_rev3_undo.dxf` | 격벽 되돌림 | rev1과 같은 상태(가시거리 위반 유지) → 두 규칙 모두 캐시 재사용, 재계산 0 |
| `S140_rev4_three_tiers.dxf` | 컨테이너 3단(상단 z=21.4 m) | 전부 기준 충족 |

실제 조선소 도면은 포함하지 않습니다. 샘플은 모두 합성 도면입니다(`engine/scripts/make_samples.py`로 재생성).

## 설계 원칙
- **판정은 엔진만** — C# 플러그인은 추출·호출·표시만 한다. 생성형 AI가 pass/fail을 정하지 않는다.
- **모르면 멈춘다** — 입력 누락은 '입력 누락', 단위 불명·모순·오타 의심은 '검토 불가'. 하나라도 있으면 전체가 충족(파랑)이 되지 않는다.
- **스냅샷이 진실** — 매번 GA_ 레이어 전체를 보내고, 무엇이 바뀌었는지는 엔진이 계산한다(Undo·Redo 어떤 경로든 동기화).
- **표시는 임시 그래픽만** — 위반 표시 때문에 도면 객체의 색·레이어를 바꾸지 않는다.
- **도면은 PC 밖으로 나가지 않는다** — 엔진은 `127.0.0.1` 전용.

## 현재 상태 (2026-10-18)
- 엔진 0.4.0: 실도면 연결(축척 1/N·측면도 영역·객체/레이어 역할·블록 펼침·LBP 대조·준비 상태·치수표 읽기 `/v1/particulars`). 엔진 0.3.0: 요소(객체)별 판정, 버전 비교(`/v1/compare`, `python -m garc compare`). 테스트 140개 통과(Python 3.10~3.14 — GitHub Actions가 push·PR마다 자동 실행), 24건 기대값은 독립 재계산(정확 산술)과 일치 — 팀원 수기 검산은 진행 중. 오류 수정과 공개 준비는 Sonnet 독립 검증 1~7차를 거쳤다 — [검증 기록](docs/review/2026-10-12_오류수정_검증기록.md)
- 플러그인: v0.3.1 AutoCAD 2027 빌드·실행 확인. v0.4.0(버전 비교·로고 패널·마우스 오버)·v0.5.0(실도면 연결)은 AutoCAD 2027 참조로 **컴파일 확인**, AutoCAD 안 실행 확인은 진행 중 — [autocad/README.md](autocad/README.md) 확인표 15~35
- 실도면: 팀이 가진 실제 GA 5건(저장소에는 넣지 않음)에서 치수표·축척 5/5 자동 추출, 탱커 GA 1건은 역할 지정 후 엔진 판정까지 확인(장애물 7천~1만여 객체, 계약+판정 0.3~1.1 초). 마스트·크레인 같은 좁은 구조물을 수면 가시거리 장애물로 볼지는 확인 필요 — [규칙 정의](docs/rule-definitions.md)
- 데모: `samples/S140_v1_review.dxf`(위반 3건) → `S140_v2_fixed.dxf`(수정 완료) + 버전 기록 `S140_v2_fixed.garc/` — AutoCAD에서 `S140_v2_fixed.dxf`를 열고 `GARC` → `GARCCOMPARE`, 또는 `python -m garc compare ../samples/S140_v1_review.dxf ../samples/S140_v2_fixed.dxf`
- 한계: 측면도(중심선)만 검토(좌우 10°·평면 미검토), 범위 안 입력 오타는 감지 못 함, 실도면의 역할 지정은 사람이 한다(자동 분류 없음) — [docs/input-contract.md](docs/input-contract.md)

## 문서
| 문서 | 내용 |
|---|---|
| [CLAUDE.md](CLAUDE.md) | 구조·원칙·일정·미결 사항 (Claude로 작업할 때 먼저 읽는 문서, 모델 사용 규칙 포함) |
| [CONTRIBUTING.md](CONTRIBUTING.md) | 브랜치·커밋·PR·테스트 규칙 |
| [docs/input-contract.md](docs/input-contract.md) | 단위·좌표·레이어(`GA_*`)·도면 특성(`GARC_*`) 계약 |
| [docs/rule-definitions.md](docs/rule-definitions.md) | L·관측점·수선·흘수 정의, 원문 근거 |
| [docs/test-cases.md](docs/test-cases.md) | 24건 기대 결과표 (자동 생성) |
| [docs/mentoring/](docs/mentoring/) | 멘토링 피드백 정리 |
| [docs/review/](docs/review/) | 오류 수정·검증 기록 |
| [docs/claude-onboarding-prompt.md](docs/claude-onboarding-prompt.md) | 팀원이 Claude에 작업을 맡길 때 붙이는 첫 메시지 |

## 팀 (홍대도면반)
| 이름 | 역할 |
|---|---|
| 표인범 (팀장) | 기획·설계 — 조선 규정 검토, GA 도면 분석 |
| 손종민 | 개발·데이터 — 플러그인 개발, 도면 데이터 처리 |
| 복준용 | 설계·검증 — 선박설계 지식 기반 기능 검증 |
| 서용언 | 디자인 — UI/UX, Figma |

## 라이선스
아직 정하지 않았습니다. 라이선스가 정해지기 전까지 이 저장소의 코드·문서 저작권은 팀에 있으며, 열람은 자유지만 재사용·배포는 팀에 먼저 문의해 주세요.
