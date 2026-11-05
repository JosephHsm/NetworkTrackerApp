# 핸드오버 연구 재실행·보고서 안내

> 2026-11-05: 이 문서와 코드는 `analysis/research_v1/`로 옮겨졌다(1차 시도 보관). 아래 `analysis/...` 경로는 `analysis/research_v1/...`로 읽는다. 새 연구 설계는 `viz_build/research_architecture.html`.

현재 비교 대상은 Q, DQN, PPO, CQL, DQN+XGBoost, DQN+RNN, DQN+LSTM, DQN+GRU, Rule입니다.
예측기 네 종류가 동일한 과거 12행으로 10초 뒤 관측 서빙 RSRP 변화량을 예측해 같은 DQN 입력에 들어갑니다.
기존 10/28 예측 비교와 11/01 policy_v2 최종 평가는 별도 기록으로 보존합니다.

## 실행 환경과 데이터

- 검증 환경: Python 3.14, NumPy/pandas, PyTorch 2.14.1 CPU, XGBoost 3.4.1. CPU 스레드 2개.
- 입력: 저장소의 `trackingcsv/new/`. 정확한 31개 파일·SHA-256·분할은 `analysis_output/policy_v2/manifest.json` 참고.
- 10/16 도보·지하철, 10/25 차량의 세 원본 CSV는 기존 로컬 작업 파일이며 이번 코드 커밋에 새로 포함하지 않았습니다. 다른 PC에서 학습을 재현하려면 이 원본을 별도로 확보해야 합니다. 기존 파일·지도 보정 작업은 삭제하거나 변경하지 않습니다.
- 결과 루트: `NETWORK_TRACKER_RESEARCH_OUTPUT` 환경변수로 지정합니다. 현재 PC는 기존 Codex outputs를 자동 사용하고, 해당 폴더가 없는 환경은 `analysis_output/research_exports/`를 기본값으로 사용합니다.

```powershell
$env:NETWORK_TRACKER_RESEARCH_OUTPUT = 'C:\path\to\research_exports'
python -B -m unittest discover -s analysis -p 'test_*policy*.py' -v
python -B analysis/model_policy_sweep.py
python -B analysis/verify_model_policy_sweep.py
python -B analysis/refresh_research_brief.py
```

## 탐색·확인 방식

- 모든 계열은 고정된 같은 학습/검증 세션과 후보 4개·선별 시드 2개를 사용합니다.
- 신규 후보를 동결하고 기존 설정과 새 확인 시드 5개를 쌍으로 비교합니다. 시드를 최적화 대상으로 고르지 않습니다.
- 약전계 증가 없음과 RSRP 손실 ≤1 dB를 우선하며, 그 안에서 HO/핑퐁 감소를 같은 비중으로 비교합니다.
- 평균 선택 기준 개선 >0.002와 시드별 과반 승리일 때 교체합니다. 통계적 유의성의 증명은 아닙니다.
- 최초 라운드 공통 anchor 표는 DQN 보상·학습률·크기·시드를 고정하고 예측기만 바꾼 비교입니다. 각자 튜닝한 결과와 구분합니다.
- 정책 12 epoch·시계열 예측 8 epoch가 현재 고정 예산입니다. 학습 수렴이나 전역 최적성까지 확인한 것은 아닙니다.
- 후보 수는 같아도 예측기 추가 학습, CQL의 고정 합성 버퍼, PPO의 새 행동 수집 등 계산량은 다릅니다. 시간·학습 재생 행 수를 원자료에 기록합니다.
- 5개 검증 분할은 서로 겹칠 수 있습니다. 새 표는 분할별 변화율 평균이며 독립 외부 테스트나 새로운 95% 신뢰구간이 아닙니다.

## 체크포인트와 자동화

`model_policy_sweep.py`는 한 번에 전체 9계열×5분할을 비교하고, 30분을 넘으면 조합 단위 체크포인트에서 종료합니다.
같은 명령을 실행하면 완료한 조합을 건너뛰고 재개합니다. 공통 `policy_continuous/batch.lock`으로 이전 탐색과 중복 실행을 막습니다.
진행 중인 라운드는 전체 모델 순위에 합산하지 않습니다. 원본 데이터나 학습 코드 해시가 바뀌면 기존 연구에 섞지 않고 중지합니다.

현재 자동화는 매시간 탐색, 약 3시간마다 `refresh_research_brief.py --if-due`로 문서·스냅샷을 갱신합니다.
문서 생성기는 `research_stage_report.py`, `sync_research_hub.py`를 호출합니다.
로컬 예약 실행에는 PC와 앱이 실행 중이어야 합니다. 실행 기록의 공백을 정상 실행 횟수로 소급 계산하지 않습니다.

## 자료 위치

- 기존 모음: `viz_build/index.html`, 생성 원본: `viz_build/index.template.html`.
- 전체 공통 비교: `viz_build/all_model_policy.html`.
- 초기 모델 비교: `viz_build/model_research.html`, `analysis/model_compare.py`, `analysis_output/stats/model_compare.json`.
- 기존 정책 연구: `viz_build/policy_research.html`, `analysis/policy_research.py`, `analysis_output/policy_v2/`.
- 쉬운 설명·4단계 진행: `viz_build/research_progress.html`, `viz_build/research_stages.html`.
- 실행 원자료: 결과 루트의 `all_model_policy/round_XXXX/`. 코드 스냅샷·분할·시드·선별·확인·결정·검증 JSON을 보존합니다.
- 이번 완료 라운드의 JSON 근거는 `analysis_output/all_model_policy/`에도 보관합니다. 모델 가중치·예측 캐시·잠금·시험 실행은 로컬 자료로 유지합니다.

## 해석 주의

약전계 변화는 RSRP ≤−105 dBm인 신선한 관측 행 비율의 모델−실제 망 차이(%p)입니다.
음수는 약한 신호 관측 감소, 양수는 증가이며 실제 지속 시간이나 연결 끊김 비율과 같지 않습니다.
현재 예측 목표는 관측 서빙 신호이며 가상 정책이 유지한 셀의 미래가 아닙니다.
새 날짜·경로의 독립 평가와 재생 환경 가정의 민감도 분석은 아직 남아 있습니다.
Jev 결제·유료 API·외부 업로드·앱 변경은 이 연구 실행에 포함하지 않습니다.
