# 핸드오버 정책 심화 연구 — 2026-11-01

평가 평균에서 신호 보호 조건을 만족한 정책 중 DQN의 HO·핑퐁 균형 점수가 가장 높았습니다.

핸드오버 23.8%, 핑퐁 86.1% 감소. 평균 RSRP 손실 0.22 dB, 약전계 비율 변화 -0.016%p입니다.

## 평가 결과

| 정책 | HO 감소 | 핑퐁 감소 | 약전계 변화 (%p) | 평균 RSRP 손실 (dB) | 강제 이동 |
|---|---:|---:|---:|---:|---:|
| 실제 망 | 0.0% | 0.0% | +0.000 | +0.00 | 0.0 |
| 고정 A3 · 3 dB | 10.8% | 36.0% | -0.153 | -0.15 | 243.0 |
| 검증으로 고른 규칙 | 22.3% | 73.1% | -0.010 | +0.32 | 337.0 |
| 표 Q-learning | 24.7% | 89.7% | +0.092 | +0.45 | 336.7 |
| DQN | 23.8% | 86.1% | -0.016 | +0.22 | 301.7 |
| DQN + GRU 예측 | 24.2% | 86.4% | +0.025 | +0.24 | 307.3 |
| CQL · 합성 고정 버퍼 | 20.0% | 66.4% | +0.035 | +0.14 | 302.3 |
| PPO | 19.4% | 84.0% | -0.070 | +0.11 | 268.3 |

## 설계와 재현

- 세션 31개, 바깥 5겹 평가, 학습 내 검증 세션 분리. 최종 정책 시드 [0, 1, 2].
- 전체 검증 후보 345개. 규칙 27개, Q 보상 27개, 신경망 보상 후보는 Q 상위 3개 공유. CQL 계수 0.1/1.
- 약전계 행 비율 증가 없음 및 평균 신호 손실 ≤1 dB 우선, 이후 HO/핑퐁 감소율 동일 가중 합으로 설정 선택.
- 평가 세션별 시드 평균 후 쌍을 맞춘 세션 부트스트랩 2,000회. 학습 분할에 조건부인 구간.
- 실행 시간 12.4분, CPU PyTorch 2.14.1+cpu.
- 코드: `analysis/policy_research.py`, 리포트: `analysis/policy_research_report.py`.
- 근거 데이터와 분할: `analysis_output/policy_v2/manifest.json`. 세션별 평가: `session_metrics.csv`.
- 상세 신뢰구간·시드별 결과·이동수단별 결과·보상 절충 그래프: `viz_build/policy_research.html`.

## 해석 한계

- 기존에 탐색한 같은 데이터로 다시 설계한 내부 평가이므로 새 날짜/경로 외부 검증 필요.
- CQL 데이터는 학습 세션에서 합성한 고정 재생 버퍼이며 실제 망 행동 로그가 아님.
- 가상 셀 누락 시 실제 서빙으로 강제 이동. 네트워크 반응·간섭·부하 변화 없음.
- 약전계는 신선한 행 기준이며 시간 비율/RLF와 다름. 같은 사용자·경로의 세션 상관 가능.
- GRU는 관측 서빙 신호 예측이며 가상 정책이 유지한 셀의 미래 예측이 아님.
- 기존 Q 코드와 할인 γ 및 적용 방법이 달라 이전 결과와 모델 효과만 비교할 수 없음.
- TTT·상태 구간·모델 크기 고정. 신경망 탐색 예산은 Q보다 작음. Jev는 미실행.

## 재실행

```powershell
python -m unittest discover -s analysis -p test_policy_research.py -v
python analysis/policy_research.py --run-name policy_v2_repeat --folds 5 --seeds 0 1 2 --search-epochs 15 --epochs 60 --neural-epochs 40 --bootstrap 2000
python analysis/policy_research_report.py --run-name policy_v2_repeat
```

방법 원문: [DQN](https://www.nature.com/articles/nature14236), [PPO](https://arxiv.org/abs/1707.06347), [CQL](https://arxiv.org/abs/2006.04779).
