# 1차 시도 연구 코드 (2026-10-28 ~ 11-04, 보관용)

2차 설계(`viz_build/research_architecture.html`)로 다시 시작하면서 1차 시도 코드를 이 폴더에 묶어 두었다.
새 실험에서 이 코드를 고치거나 이어서 쓰지 않는다. 결과 기록과 비교용으로만 보관한다.

| 파일 | 내용 | 결과 페이지 |
|---|---|---|
| `model_compare.py`, `model_compare_fig.py` | 10/28 모델 4종 비교 (핑퐁 분류, RSRP 예측, Q-learning) | `viz_build/model_research.html` |
| `policy_research.py`, `policy_research_report.py`, `test_policy_research.py` | 11/01 정책 심화 연구 (Q/DQN/PPO/CQL, 5분할×3시드) | `viz_build/policy_research.html` |
| `policy_continue.py`, `test_policy_confirmation.py` | 정책 지속 탐색·5시드 확인 | `viz_build/policy_research.html` |
| `model_policy_sweep.py`, `model_policy_sweep_report.py`, `verify_model_policy_sweep.py`, `test_model_policy_sweep.py` | DQN+예측기 결합 전체 비교 | `viz_build/all_model_policy.html` |
| `refresh_research_brief.py`, `research_stage_report.py`, `sync_research_hub.py`, `research_paths.py` | 3시간마다 돌던 쉬운 설명·4단계 문서 자동 갱신 | `viz_build/research_progress.html`, `research_stages.html` |
| `RESEARCH_RUNBOOK.md` | 1차 시도 재실행 안내 (경로는 옮기기 전 기준) | |

- 결과 데이터는 원래 위치 그대로 둔다: `analysis_output/policy_v2/`, `analysis_output/stats/model_compare*.json`.
- 공용 모듈(`analysis/ho_review.py`, `pp_feasibility.py`)은 옮기지 않았다. 여기 코드는 상위 폴더를 import 경로에 추가해 사용한다.
- 다시 돌릴 때: 저장소 루트에서 `python -B analysis/research_v1/<파일>.py`. 테스트: `python -B -m unittest discover -s analysis/research_v1 -p "test_*.py"`.
- Codex 예약 작업(매시간 탐색, 3시간 문서 갱신)은 정지 상태다. 옛 경로(`analysis/*.py`)를 가리키므로 다시 켜도 동작하지 않는다.
