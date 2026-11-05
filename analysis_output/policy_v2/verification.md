# 핸드오버 정책 심화 연구

2026-11-01 완료. 31개 세션, 바깥 5분할·최종 정책 3시드, 검증 후보 345개.

- `핸드오버_정책_심화연구.html`을 브라우저로 열면 됩니다. 그래프와 결과는 HTML에 포함되어 인터넷 연결이 필요 없습니다.
- 기존 모음: `C:\Users\admin\NetworkTrackerApp\viz_build\index.html` → 연구 → 핸드오버 정책 심화 연구.
- 새 연구 페이지: `C:\Users\admin\NetworkTrackerApp\viz_build\policy_research.html`.
- 원래 10/28의 연구 페이지는 유지하고 후속 연구 링크를 추가했습니다.
- 코드 루트: `C:\Users\admin\NetworkTrackerApp`.
- 새 실험: `analysis/policy_research.py`. 새 리포트: `analysis/policy_research_report.py`. 검증: `analysis/test_policy_research.py`.
- 실행 근거: `analysis_output/policy_v2/`의 manifest/search/choices/records/summary/curves JSON, session_metrics.csv, 분할·시드별 가중치.

DQN 평가 평균: HO 23.8% 감소, 핑퐁 86.1% 감소, 평균 RSRP 손실 0.22 dB. 약전계 변화 −0.016%p의 95% 구간은 약 [−0.282, +0.210]%p이므로 약전계 악화가 없다고 확정할 수 없습니다. 4개 목표를 모두 충족한 정책은 없습니다.

CQL은 학습 세션에서 합성한 고정 재생 버퍼를 사용했습니다. 실제 망의 오프라인 행동 로그 성과가 아닙니다. Jev 호출·결제는 하지 않았습니다.

## 검증과 범위

- 환경 할인/종료 보상, 분할 분리, 상태 72개, numpy/torch 추론 일치 등 5개 단위 검증 통과.
- 원본 31개 세션 모두 고정 A3의 집계 7개 필드가 기존 재생 코드와 정확히 일치.
- 최종 평가 558개 고유 행·검증 후보 345개, 코드와 원본 데이터 31개의 SHA-256 일치 확인.
- HTML 스크립트 실행과 정책 점 8개·분할별 검증 점 69개, 필터·선택 동작 검증 통과.
- PC 브라우저 접근 승인이 시간 초과되어 실제 브라우저 화면/모바일 레이아웃 육안 검수는 완료하지 못했습니다.
- 기존 작업을 커밋하거나 덮어쓰지 않았으며, 새 실험은 별도 경로로 저장했습니다.

보관용 ZIP에는 코드·결과·가중치와 데이터 해시를 넣었습니다. 위치 좌표를 포함한 원본 CSV는 중복 포함하지 않았습니다. 재실행에는 원래 저장소의 trackingcsv/new 데이터가 필요합니다.
