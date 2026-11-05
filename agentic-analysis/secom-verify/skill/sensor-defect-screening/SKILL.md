---
name: sensor-defect-screening
description: Screens many process sensors or parameters against pass/fail labels to find which ones are associated with defects, while guarding against false discoveries from multiple comparisons, near-constant sensors, and time clustering of defects. Use when asked to find sensors, process parameters, or equipment variables that cause or relate to defects, yield loss, failures, or 불량 원인 in tabular manufacturing or semiconductor data with dozens to hundreds of columns. Do NOT use for wafer map image classification, single-variable control charts, or data without a pass/fail label.
---

# 센서-불량 연관 스크리닝

## 왜 이 절차를 따르는가

센서가 수백 개인 데이터에서 "불량 원인 센서 찾기"는 거의 항상 뭔가를 찾아낸다. 찾아낸 것이
진짜인지는 별개다. 세 가지 착시가 반복된다.

1. **다중비교** — 센서를 하나씩 검정하면 불량과 전혀 무관해도 α=0.05에서 약 5%가 유의로
   나온다. 센서 500개면 우연만으로 25개 안팎이다. p<0.05 목록은 그 자체로 증거가 아니다.
2. **사실상 상수인 센서** — 대부분 같은 값이고 몇 행만 다른 센서는, 그 몇 행이 우연히 불량
   행에 걸리면 점근 근사 p값이 극단적으로 작아진다. 검정의 적용 조건을 벗어난 경우다.
3. **시간 교란** — 불량이 특정 시기에 몰려 있으면, 시간에 따라 서서히 변하는 센서는 불량
   원인이 아니어도 불량과 상관을 보인다.

이 세 가지는 코드로 계산해야 판단할 수 있으므로, 직접 검정을 짜기 전에 번들 스크립트를 먼저
실행한다. 스크립트가 계산하고, 너는 결과를 해석하고 보고한다.

## 절차

아래 체크리스트를 복사해 진행 상황을 표시한다.

```
- [ ] 1. 입력 형태 맞추기
- [ ] 2. screen.py 실행
- [ ] 3. verdict 해석
- [ ] 4. 결론 작성
- [ ] 5. check_claims.py로 검토 — 통과해야 제출
```

**1. 입력 형태 맞추기**

`screen.py`는 두 CSV를 받는다. 형태가 다르면 먼저 변환한다.
- 센서 CSV: 헤더 있음, 행 = 제품/웨이퍼, 열 = 센서, 결측은 빈 칸 또는 NaN
- 라벨 CSV: `label`(-1=양품, 1=불량), `timestamp` 두 열. 행 순서가 센서 CSV와 같아야 한다

**2. 스크리닝 실행**

이 스킬 디렉터리의 `scripts/screen.py`를 실행한다 (pandas, numpy, scipy, statsmodels 필요).

```bash
python3 <이 스킬 디렉터리>/scripts/screen.py --sensors sensors.csv --labels labels.csv --out screen.json
```

**3. verdict 해석**

| verdict | 의미 | 보고 방법 |
|---|---|---|
| `NO_EVIDENCE` | p<0.05 센서 수가 라벨을 섞었을 때의 범위 안이고, BH-FDR 보정 후 0개 | 원인 센서를 나열하지 않는다. 라벨과 구별 가능한 연관이 없다고 보고한다 |
| `NO_VALIDATED_CANDIDATES` | BH 보정은 통과했으나 시간 구간 안에서 비교하면 남는 센서가 없다 | 원인으로 보고하지 않는다. 시기 효과(설비 변경·자재 로트 등) 조사를 권고한다 |
| `CANDIDATES` | BH 보정과 시간 보정을 모두 통과한 센서가 있다 | `validated_candidates`만 연관 후보로 보고한다 |

`screen.json`에서 함께 볼 값:
- `noise_floor.raw_p_lt_0.05_mean` / `_p95` — 우연만으로 나오는 p<0.05 센서 수. 보고서에 관측값과 나란히 적는다
- `time_structure.chi2_p_fail_rate_homogeneous` — 작으면 불량이 시기에 몰려 있다
- `lost_to_time_adjustment` — 전체 비교에선 유의했지만 시간 보정에서 사라진 센서. 원인 목록에 넣지 않고 따로 언급한다

**4. 결론 작성**

- 원인 센서 목록에는 `validated_candidates`에 있는 센서만 넣는다. 그 목록이 비어 있으면 원인 목록도 비운다
- p<0.05만 통과한 센서를 원인으로 올리지 않는다. 몇 개가 나왔는지는 노이즈 바닥과 함께 보고한다
- 통계적 연관은 인과가 아니다. "원인"이 아니라 "연관 후보"로 쓰고, 확인 방법(설비 이력 대조, 확인 실험)을 제안한다
- 직접 추가 분석(모델 기반 중요도 등)을 했다면 참고로 적되, 원인 목록의 근거로 쓰지 않는다. 트리 모델 중요도도 같은 다중비교·시간 교란을 피하지 못한다

**5. 검토**

결론 파일을 쓴 뒤 실행한다. 스크리닝이 뒷받침하지 않는 센서가 원인 목록에 있으면 실패한다.

```bash
python3 <이 스킬 디렉터리>/scripts/check_claims.py --claims result.json --screen screen.json
```

실패하면 지적된 센서를 원인 목록에서 빼고 다시 실행한다. 통과한 뒤에 제출한다.
결론 파일 형식이 다르면 `--key`로 센서 목록 경로를 지정한다 (기본: `cause_sensors[].sensor`).
