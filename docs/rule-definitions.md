# 규칙 정의와 원문 근거

> 멘토링 1차 확인 항목 1: "L·관측점·수선·흘수의 정확한 정의와 적용 규정/판본"
> ⚠ 판본 확인 필요: 아래 원문은 공개 IMO 문서·imorules 통합본 기준으로 확인했다. **팀이 보유한 SOLAS 통합본(연도)과 KR 규칙 판본으로 조항 번호·문구를 대조**하고 이 표의 '판본' 칸을 확정할 것 (담당: 표인범).

## 두 규칙에서 "L"이 다르다
| | 가시거리 (SOLAS V/22) | 충돌격벽 (SOLAS II-1/12) |
|---|---|---|
| L | **전장 LOA** — V/2.4 "Length of a ship means its length overall" | **만재흘수선협약 길이** — II-1/2: ICLL 정의 (최소 형심 85% 수선 길이의 96%, 또는 그 수선에서 선수재 전면~타두재 중심 중 큰 값) |
| 도면 특성 | `GARC_LOA` | `GARC_L_LL` |
| 쓰이는 곳 | 적용 기준(55 m), 허용 사각 거리 min(2L, 500 m) | 하한·상한, 구상선수 기준점 0.015L |

같은 배라도 두 값이 다르므로(샘플 S140: LOA 140 m / L 132 m) 하나로 통일하면 틀린다.

---

## R1. SOLAS-V-22.1.1 선교 전방 수면 가시거리
**원문 (V/22.1 서두, .1)**
> Ships of not less than 55 m in length, as defined in regulation 2.4, constructed on or after 1 July 1998, shall meet the following requirements:
> .1 The view of the sea surface from the conning position shall not be obscured by more than two ship lengths, or 500 m, whichever is less, forward of the bow to 10° on either side under all conditions of draught, trim and deck cargo;

| 용어 | 엔진 정의 | 근거·비고 |
|---|---|---|
| 적용 | LOA ≥ 55 m 이고 용골 거치일 ≥ 1998-07-01 | V/22.1 서두. 거치일 미입력 시 신조선으로 가정하고 근거에 기록. 거치일 1950 이전·형식 오류는 오타로 보고 '검토 불가'(적용 제외로 넘기지 않음, 오타 감지용) |
| 관측점(conning position) | GA_EYE 점(선교 갑판 높이) + 눈높이 1.8 m | 눈높이 1,800 mm는 영국 SI 1998/1419 Reg.5(1)(h) 등 각국 이행 규정·IMO 지침(MSC/Circ.982)에서 쓰는 값. **조종위치 자체의 정의는 MSC/Circ.982 원문으로 확인 필요** |
| 선수(bow) | GA_HULL 꼭짓점 중 x 최대 | "forward of the bow" — 사각 거리의 시작점 |
| 수선 | z = 기선 + 흘수, AP·FP 흘수 사이 직선(트림 반영)을 선수 앞으로 연장 | "all conditions of draught, trim" |
| 흘수 조건 | `GARC_CONDITIONS`의 모든 조건 중 **최악값**으로 판정 | 통상 밸러스트 도착(선미 트림)이 최악 |
| 장애물 | GA_HULL + GA_OBST의 꼭짓점 중 눈보다 앞·수면보다 위 | 다각형 그림자의 극값은 꼭짓점에서 생김 |
| 사각 거리 D | 눈→장애물 꼭짓점 시선을 연장해 수면과 만나는 x의 최댓값 − 선수 x | 장애물이 눈높이 이상이면 D = ∞ (위반) |
| 기준 | D ≤ min(2·LOA, 500 m), **경계 포함** | 비교 시 1 µm 부동소수점 보정 |

**수식**: 눈 E=(xe, ze), 장애물 O=(xo, zo), 수선 z = a + b·x
 t = (a + b·xe − ze) / ((zo − ze) − b·(xo − xe)), x_hit = xe + t·(xo − xe)
 등흘수(b=0)면 닮은 삼각형: x_hit = xe + (xo − xe)·(ze − T)/(ze − zo)

**위반 시 제안(계산값)**: 최악 조건(사각 거리 최대)의 결정 장애물에서, **입력된 모든 흘수 조건**을 만족하는 허용 높이
 z_lim(x) = min over 조건 c [ ze + (x − xe)·(z_wl,c(x_bow+한계) − ze)/(x_bow+한계 − xe) ]
 결정 장애물의 꼭짓점 중 z > z_lim(x)인 것들에서 z_lim이 가장 낮은 꼭짓점(xo, z_max = z_lim(xo))을 제안한다.
 (사각 거리가 무한대일 땐 '처음 만난 꼭짓점'이 결정 꼭짓점이 되므로, 한 꼭짓점만 보면 그리는 순서에 따라 제안이 달라졌다.)
 표시값은 cm 단위로 **내림**(그대로 적용해도 기준을 넘지 않게), 그리고 항상 눈높이보다 1 cm 이상 낮게(눈높이 이상 장애물은 시선을 막으므로).
 충돌격벽의 "… m 이상 옮기세요"는 **올림**. 계단·리세스 폭(d_max − d_min)이 허용 구간 폭보다 넓으면 이동량 대신 "이동만으로는 해결되지 않음"을 낸다.
 (사각 거리가 가장 큰 조건과 허용 상단이 가장 낮은 조건은 다를 수 있다 — 수선 직선끼리 x_bow+한계 앞쪽에서 교차하는 경우.
  2026-10-12 이전에는 최악 조건 하나로만 계산해 다른 조건에서 여전히 위반일 수 있었음.)
 결정 장애물 외에 단독으로 허용 한계를 넘는 객체가 있으면 제안 문구와 대상(targets)에 함께 넣는다.

**실도면에서 장애물로 무엇을 지정할지 (2026-10-18 실도면 시험에서 나온 쟁점 — 확인 필요)**
- 엔진은 측면도의 장애물을 '그 꼭짓점 아래가 막힌 실체'로 본다(갑판 화물·불워크·선수루처럼). 그래서 **폭이 좁은 마스트·크레인 지브도
  중심선 위 시선을 막는 것으로 계산**된다. 실도면 시험(탱커 GA 1건)에서 갑판 크레인 지브 끝(눈높이보다 0.8 m 낮음)이나
  선수 마스트를 장애물로 넣으면 사각 거리가 2 km 이상으로 '위반', 빼면 145~163 m로 '기준 충족'이었다(설계·구조 흘수 등흘수 조건만, 어느 블록을 빼느냐에 따라).
  같은 도면에서 트림이 있는 밸러스트 조건을 넣으면 결과가 더 불리해진다 — 도면 치수표에는 밸러스트 흘수가 없으므로 사람이 넣어야 한다.
- 실제로는 좁은 구조물 뒤의 수면도 좌우로는 보이므로, 이런 구조물을 V/22.1.1(수면 가시거리)이 아니라 V/22.1.2의 **사각 섹터(blind sector)**
  요건으로 따로 보는 것이 관행일 수 있다 — **근거 미확인. 선급·IMO 지침(MSC/Circ.982 등) 원문과 현업 관행으로 확인할 것** (담당: 표인범).
- 확인 전까지의 운용: 장애물 지정은 사람이 하고(역할 지정), 결과 카드의 '결정 객체'를 보고 그것이 좁은 구조물인지 판단한다.
  엔진은 어느 쪽으로도 자동으로 빼지 않는다(모르면 멈춘다·안전 쪽).
- 조타실 자체(창틀·지붕 차양)는 조종위치보다 앞에 있어도 장애물로 지정하지 않는다(눈보다 높은 차양을 넣으면 '시선이 수면에 닿지 않음'이 됨).

**다루지 않는 것 (화면·발표에 명시)**
- 좌우 10° 범위·평면상 장애물 폭 (측면도만 검토)
- V/22.1.2 사각 섹터 각도(개별 10°, 합계 20°, 사이 5°), .3 이후 수평 시야 요건
- 입력된 흘수 조건이 "모든 적재 조건"을 대표하는지 여부

## R2. SOLAS-II1-12 충돌격벽(선수격벽) 위치
**원문 (II-1/12.1, 12.3, 12.4 — imorules 통합본)**
> 12.1 … This bulkhead shall be located at a distance from the forward perpendicular of not less than 0.05L or 10 m, whichever is the less, and, except as may be permitted by the Administration, not more than 0.08L or 0.05L + 3 m, whichever is the greater.
> 12.3 Where any part of the ship below the waterline extends forward of the forward perpendicular, e.g. a bulbous bow, the distances stipulated in paragraph 1 shall be measured from a point either: .1 at the mid-length of such extension; .2 at a distance 0.015L forward of the forward perpendicular; or .3 at a distance 3 m forward of the forward perpendicular, whichever gives the smallest measurement.
> 12.4 The bulkhead may have steps or recesses provided they are within the limits prescribed in paragraph 1 or 3.

| 용어 | 엔진 정의 | 근거·비고 |
|---|---|---|
| L | `GARC_L_LL` (ICLL 길이) | II-1/2 정의. LOA 아님 |
| FP | `GARC_X_FP` | L의 선수 끝 수직선 (II-1/2) |
| 기준점 | 구상선수(GA_BULB) 최전단이 FP보다 앞이면 FP 전방 min(돌출길이/2, 0.015L, 3 m) | 12.3 "smallest measurement" |
| 측정값 d | 기준점 − 격벽 x. 계단형이면 모든 꼭짓점의 최소~최대 | 12.4 |
| 기준 | min(0.05L, 10 m) ≤ d ≤ max(0.08L, 0.05L + 3 m), 경계 포함 | 상한 초과는 "주관청 허용 예외 가능 → 사람 확인" 문구 |

**다루지 않는 것**: 수밀 높이(건현갑판까지), 구상선수가 실제 "수선 아래"인지 판단(GA_BULB에 그린 것을 그대로 믿음), 주관청 예외, 여객선 별도 요건, KR 선급규칙 추가 요건.

---

## 출처
- SOLAS II-1/12 통합 원문: https://www.imorules.com/GUID-8F05CA12-925C-4AE7-8115-CA66EBA51112.html
- SOLAS V/22.1 서두·V/2.4 길이 정의: IMO Res. MSC.142(77) https://wwwcdn.imo.org/localresources/en/KnowledgeCentre/IndexofIMOResolutions/MSCResolutions/MSC.142(77).pdf
- 눈높이 1,800 mm·V/22 국내 이행 예: UK SI 1998/1419 https://legislation.gov.uk/uksi/1998/1419/made/data.html
- 2L·500 m·흘수/트림/갑판화물 조건 요약: AMSA Marine Notice 14/2022 https://www.amsa.gov.au/about/regulations-and-standards/marine-notice-14-2022-ensuring-navigation-bridge-visibility
