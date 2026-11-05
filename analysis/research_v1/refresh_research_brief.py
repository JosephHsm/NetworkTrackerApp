"""Readable 3-hour research briefs; immutable archives and a stable latest URL."""
import argparse, html, json
from datetime import datetime, timezone, timedelta
from pathlib import Path

from research_paths import ROOT, EXPORT as OUT
STATE=OUT/'policy_continuous'
def load(p):return json.loads(p.read_text(encoding='utf-8'))
def save(p,text):
    p.parent.mkdir(parents=True,exist_ok=True)
    tmp=p.with_suffix(p.suffix+'.tmp');tmp.write_text(text,encoding='utf-8');tmp.replace(p)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--if-due',action='store_true');args=parser.parse_args()
    now=datetime.now(timezone(timedelta(hours=9)))
    marker=STATE/'last_readable_report.json'
    prior=load(marker) if marker.exists() else {}
    if args.if_due and prior and now.timestamp()-prior['timestamp']<3*3600:
        print('NOT_DUE: readable report is refreshed every 3 hours.');return
    state=load(STATE/'state.json');summary=load(ROOT/'analysis_output/policy_v2/summary.json')['policies']
    history=state['history'];new=[h for h in history if h['batch']>prior.get('last_batch',0)]
    changes=sum(h['updates'] for h in new);configs=sum(h['trials'] for h in new)
    when=now.strftime('%Y-%m-%d %H:%M KST');archive=now.strftime('%Y%m%d_%H%M%S')
    if new:
        interval=f'지난 문서 이후 {len(new)}개 배치를 완료했습니다. 후보 설정 {configs}개를 비교했고, 분할별 보상 설정을 {changes}곳 교체했습니다.'
    else:
        interval='지난 문서 이후 새로 완료된 실험은 없습니다. 아래 최신 결과와 결론을 유지합니다. 진행 중이거나 실패한 실험을 완료 결과로 합산하지 않았습니다.'
    status='12개 배치 연속 기준 이상 개선이 없어 추가 계산을 쉬는 상태입니다. 검증 설계 변경이나 새 독립 평가가 필요합니다.' if state.get('status')=='plateau' else '검증 데이터에서 보상 설정을 추가 탐색 중입니다. 아직 최적 설정을 확정한 단계는 아닙니다.'
    inclusive_path=OUT/'all_model_policy/state.json'
    inclusive=load(inclusive_path) if inclusive_path.exists() else None
    if inclusive:
        from model_policy_sweep_report import generate as refresh_model_report
        refresh_model_report(OUT/'all_model_policy')
    inclusive_note='전체 예측기 결합 비교는 아직 시작하지 않았습니다.'
    since=[]
    model_scope='XGBoost·RNN·LSTM·GRU는 10/28 예측 비교를 마쳤고, 이전 정책 심화에는 GRU 결합만 포함했습니다. 이들은 너무 커서 제외한 것이 아닙니다.'
    inclusive_table='새 공통 비교의 완료 결과는 아직 없습니다.'
    if inclusive:
        count=inclusive['completed_rounds'];active=inclusive.get('current_round')
        status='전체 예측기·정책의 공통 비교를 진행하고 있습니다. 기존 Q 중심 순환 탐색은 과거 이력으로 보존합니다.'
        latest=inclusive['history'][-1] if inclusive['history'] else None
        since=[r for r in inclusive['history'] if r['round']>prior.get('inclusive_last_round',0)]
        if since:
            interval=f'지난 설명 문서 이후 전체 모델 비교 {len(since)}개 라운드를 완료했습니다. 선별 설정 {sum(r["screening_configurations"] for r in since)}개와 확인 설정 {sum(r["confirmation_configurations"] for r in since)}개를 비교했고, 분할별 설정을 {sum(r["updates"] for r in since)}곳 교체했습니다.'
        else:
            interval='지난 설명 문서 이후 새로 완료된 전체 비교 라운드는 없습니다. 진행 중인 라운드는 완료 횟수와 결과 표에 합산하지 않았습니다.'
        inclusive_note=f'새 공통 비교는 완료 라운드 {count}개입니다. 지난 설명 문서 이후 완료 라운드 {len(since)}개를 추가했습니다.'
        if latest:
            inclusive_note+=f' 마지막 완료 라운드는 9계열×{latest["folds"]}분할, 선별 설정 {latest["screening_configurations"]}개, 확인 설정 {latest["confirmation_configurations"]}개, 정책 학습 {latest["policy_fit_count"]}회, 실행 시간 합계 {latest["seconds"]/60:.1f}분입니다.'
        if active:
            p=OUT/'all_model_policy'/f'round_{active:04d}'/'progress.json'
            progress=load(p) if p.exists() else {'completed_pairs':0,'total_pairs':45}
            inclusive_note+=f' 현재 라운드 {active}는 {progress["completed_pairs"]}/{progress["total_pairs"]}개 조합 완료로, 전체 순위에 합산하지 않았습니다.'
        model_scope='현재는 **DQN / DQN+XGBoost / DQN+RNN / DQN+LSTM / DQN+GRU**를 비교하며, Q-learning·PPO·CQL·규칙도 같은 후보 수와 확인 시드로 함께 탐색합니다. 예측기 4종은 같은 과거 12행 입력으로 10초 뒤 관측 신호 변화량을 예측합니다. 예측기만 바꾼 공통 설정 표와 각 조합이 자기 검증 결과로 튜닝한 표를 구분합니다. Q의 우수 보상 설정을 다른 모델의 후보로 가져오지 않습니다.'
        summary_path=OUT/'all_model_policy/latest_summary.json'
        if summary_path.exists():
            combined=load(summary_path)
            if combined.get('complete'):
                inclusive_table='| 조합 | HO 감소 | 핑퐁 감소 | 약전계 변화 (%p) | RSRP 손실 (dB) |\n|---|---:|---:|---:|---:|\n'+ '\n'.join(f'| {k} | {v["ho_reduction"]:.1%} | {v["pp_reduction"]:.1%} | {v["weak_delta_pp"]:+.3f} | {v["rsrp_loss"]:+.2f} |' for k,v in combined['family_results'].items())
    gaps=[]
    for older,newer in zip(history,history[1:]):
        plan_path=STATE/f'batch_{newer["batch"]:04d}'/'plan.json'
        if not plan_path.exists():continue
        started=datetime.fromisoformat(load(plan_path)['time'])
        finished=datetime.fromisoformat(older['time'])
        hours=(started-finished).total_seconds()/3600
        if hours>2:
            gaps.append(f'{finished.strftime("%m/%d %H:%M")} 배치 완료부터 {started.strftime("%m/%d %H:%M")} 다음 정상 배치 시작까지 약 {hours:.1f}시간의 간격이 있습니다. 이 시간에 매시간 실험을 완료했다고 합산하지 않았으며, 기록만으로 공백 원인을 확정하지 않았습니다.')
    execution_note=gaps[-1] if gaps else '완료된 배치와 실행 시작 시각을 기준으로 집계했습니다.'
    sync_note='상세 HTML은 outputs의 최신본을 확인하세요. 원본 프로젝트의 연구 모음 페이지는 쓰기 권한이 없어 마지막 동기화 시점의 내용으로 남아 있습니다.' if not state.get('report_mirrored',False) else '상세 HTML은 마지막 완료 배치에서 원본 프로젝트의 연구 모음에도 동기화했습니다.'
    d=summary['DQN']['delta'];ci=summary['DQN']['ci95']
    labels={'A3':'기본 규칙 A3','Rule':'검증으로 고른 규칙','Q':'표 Q-learning','DQN':'DQN','DQN+GRU':'DQN + 신호 예측','CQL':'CQL (합성 데이터)','PPO':'PPO'}
    results=[]
    for key,label in labels.items():
        v=summary[key]['delta']
        results.append([label,f'{v["ho_reduction"]:.1%}',f'{v["pp_reduction"]:.1%}',f'{v["weak_delta_pp"]:+.3f}',f'{v["rsrp_loss"]:+.2f}'])
    recent='\n'.join(f'- 배치 {h["batch"]}: Q-learning + {h["neural_family"]}, 후보 {h["trials"]}개, 설정 교체 {h["updates"]}곳, {h["seconds"]/60:.1f}분.' for h in history[-6:])
    md=f'''# 핸드오버 연구 진행 — 쉽게 읽는 최신 설명

갱신: {when} · 과거 Q 중심 탐색 완료 배치 {state['completed_batches']}개 · 새 전체 모델 비교는 아래 별도 집계 · 이 문서는 3시간마다 갱신합니다.

## 이 연구는 무엇을 해결하려는 건가요?

이동 중 휴대폰은 접속하는 셀(기지국의 통신 구역)을 바꿉니다. 이것이 핸드오버입니다. 신호가 약해져 바꾸는 것은 필요하지만, 신호가 충분한데 자주 바꾸거나 잠깐 만에 원래 셀로 돌아오는 핑퐁은 줄이고 싶습니다.

연구 목적은 **핸드오버와 핑퐁을 줄이면서 신호 품질을 지키는 정책**을 찾는 것입니다. 안드로이드 앱으로 모은 31개 세션을 시간순으로 재생해 비교합니다. 앱이 실제 통신망의 핸드오버 결정을 바꾸는 실험은 아닙니다.

목표는 네 가지입니다: 핸드오버 30% 이상 감소, 핑퐁 80% 이상 감소, 약한 신호 비율 증가 없음, 평균 신호 손실 1 dB 이내.

## 일반적인 AI 연구 과정과 우리는 얼마나 맞나요?

전체 흐름은 맞지만, 현재는 실측 로그 재생 환경에서 후보 정책을 찾는 탐색적 실험 단계입니다. 모델을 여러 번 학습하는 작업에 더해, 비교 결과를 믿을 수 있는 이유와 적용 범위를 검증해야 합니다.

| 연구에서 확인하는 일 | 현재 우리 상태 |
|---|---|
| 연구 질문과 성공 기준을 미리 정하기 | 핸드오버·핑퐁 감소와 신호 보호의 네 목표를 정했습니다. |
| 기존 연구와 차별점을 정하기 | 관련 문헌은 모았지만, 우리만의 핵심 기여를 더 명확히 정리해야 합니다. |
| 비교 기준과 데이터 분리 | 고정 규칙·튜닝한 규칙·여러 학습 정책을 비교하며 학습/검증/평가 세션을 분리했습니다. |
| 설정 탐색과 반복 학습 | 2시드 선별 후 기존 설정과 후보를 별도 5시드로 확인합니다. |
| 왜 좋아졌는지 원인 분리 | 공통 DQN 설정에서 예측기만 바꾼 비교를 보존했습니다. 보상 항목·강제 이동·상태 특징의 제거·민감도 실험은 더 필요합니다. |
| 처음 보는 환경에서 최종 확인 | 새로운 날짜·경로의 외부 평가는 아직 없습니다. 실제 망 반응도 검증하지 않았습니다. |

특히 현재 로그에는 ‘우리가 핸드오버를 보류했다면 실제 통신망이 어떻게 반응했을지’가 직접 기록되어 있지 않습니다. 재생 환경에서 줄어든 핑퐁 수를 실제 망에서 같은 만큼 줄일 수 있다고 바로 주장할 수 없는 이유입니다. 재생 환경의 관측 누락·강제 이동 처리에 따른 민감도를 확인하는 것이 중요한 후속 실험입니다.

다음 연구 단계는 유력 설정을 고정하고, 어떤 설계가 효과를 냈는지 하나씩 제거·변경해 비교한 뒤, 새 날짜·경로 데이터에서 확인하는 것입니다. 매시간 탐색은 이 과정 중 설정 탐색을 자동화한 부분이며, 학습 실행 시간이나 누적 횟수만으로 연구가 완성됐다고 판단하지 않습니다.

방법 참고: scikit-learn의 Nested versus non-nested cross-validation, NeurIPS Paper Checklist, Google Research의 RLiable 설명. 이 자료들은 설정 선택과 평가의 분리, 실험의 재현성·불확실성 보고를 설명합니다.

## 지금 어디까지 왔나요?

{status}

{interval}

실행 기록: {execution_note}

문서 위치: {sync_note}

누적 검증 후보 평가는 {sum(h['trials'] for h in history)}개입니다. 현재 가중치는 분할과 모델마다 다르며, 공통으로 쓸 하나의 숫자를 확정하지 않았습니다.

위 배치 수와 누적 후보 수는 이전 policy_continuous 기록입니다. 새 전체 모델 비교는 별도로 집계합니다. **{inclusive_note}**

## 예측기와 정책의 어떤 부분을 바꾸나요?

{model_scope}

- 예측 모델 설정: RNN·LSTM·GRU 은닉 크기 16/32와 학습률, XGBoost 트리 깊이·개수·학습률의 제한된 조합을 탐색합니다.
- 정책 설정: 핑퐁·약전계·신호 차이 보상 비용과 정책 학습률, 표 Q 갱신률, CQL 보수 계수를 탐색합니다.
- 시드: 좋은 시드를 골라 점수를 높이지 않습니다. 같은 설정을 선별 2시드·별도 확인 5시드로 반복해 평균과 안정성을 봅니다.
- 고정 설정: 예측 입력 12행×2특징과 10초 예측 목표, DQN 크기 64×64, 할인 0.99, 현재 공통 비교의 정책 12 epoch·시계열 예측 8 epoch입니다. 학습량 충분성과 전역 최적성까지 입증한 것은 아닙니다.
- 후보 수가 같아도 예측기 추가 학습이나 CQL/PPO의 데이터 사용 방식 때문에 실제 계산량은 다릅니다. 실행 시간과 학습 재생 행 수도 함께 보존합니다.

## 이번부터는 후보를 5번 더 확인합니다

새 공통 비교의 마지막 완료 결과는 다음과 같습니다. 각 확인 시드의 평균 후 검증 분할별 변화율을 평균했습니다. 검증 분할은 서로 겹칠 수 있으므로 독립 외부 평가 합계가 아닙니다. 기존에 동결한 11/01 평가와도 별도로 읽어야 합니다.

{inclusive_table}

전체 후보는 2개 시드로 빠르게 선별하고, 그중 가장 좋은 신규 후보와 기존 설정을 **선별에 사용하지 않은 새 시드 5개로 각각 다시 학습**합니다. 비교하는 두 설정은 같은 시드를 쓰고, 다음 배치에서는 새 확인 시드로 바꿉니다.

확인 단계의 평균 개선이 기준을 넘고, 5개 중 3개 이상의 시드에서 후보의 선택 기준 점수가 더 나을 때만 설정을 교체합니다. 신호 보호 조건을 먼저 보고 그 안에서 핸드오버·핑퐁 감소를 비교하는 기준입니다. 이는 우연히 잘 나온 후보를 걸러내기 위한 장치이며 통계적 유의성이나 새 환경에서의 개선을 입증한 것은 아닙니다. 과거 2시드 배치 결과는 5시드 확인을 거친 것으로 바꾸어 적지 않습니다.

이전 정책 탐색의 완료된 5시드 확인은 {sum(h.get('confirmation_trials',0) for h in history)}개 설정 비교입니다. 새 공통 비교는 위에 별도로 집계했습니다. 후보와 기존 설정을 각각 세므로, 한 쌍의 비교는 2개 설정입니다. 상세 HTML에는 시드별 승수와 평균 수치, 설정 교체 여부를 따로 표시합니다.

## 우리가 찾는 ‘가중치’가 뭔가요?

여기서 계속 바꾸는 것은 **보상 비용의 가중치**입니다. 핸드오버 한 번의 비용은 1로 두고, 핑퐁·약한 신호·더 좋은 셀을 놓친 손해에 얼마의 벌점을 줄지 정합니다. 예를 들어 핑퐁 비용 9는 핑퐁을 특히 피하도록 학습시키겠다는 뜻입니다. 숫자가 클수록 항상 좋은 것은 아닙니다. 너무 이동을 막으면 약한 신호에 오래 남을 수 있습니다.

신경망 내부의 수많은 가중치는 각 학습 실행 안에서 자동으로 학습됩니다. 지금의 반복 탐색은 그 학습이 어떤 손해를 더 싫어하도록 할지 정하는 실험입니다.

## XGBoost·RNN·LSTM도 지금 돌리고 있나요?

이 모델들은 10/28의 핑퐁 분류·신호 예측에서 이미 비교했습니다. 초기 모델은 CPU로 실행한 작은 설정이며, 너무 커서 제외한 것은 아닙니다. 현재 전체 모델 결합 상태는 위 새 공통 비교 설명을 기준으로 보세요.

이전 policy_v2는 초기의 XGBoost 차단 정책과 RNN/LSTM 결합 정책이 빠져 있었고, 초기 신경망 보상 후보도 Q 검증 상위 설정을 공유했습니다. 새 공통 비교에서는 XGBoost·RNN·LSTM·GRU 예측 결합을 추가하고, 각 모델 자신의 검증 결과로 설정을 고르게 바꿨습니다. **새 비교도 한정된 설정과 반복 검증 데이터의 탐색이므로 전역 최적 모델을 찾았다고 판단하지 않습니다.**

상세 정책 HTML 상단의 ‘전체 모델·실행 범위’에는 완료된 예측 결과와 현재 반복 실행 여부를 구분해 표시합니다. 과거 3시드 예측 결과를 새 5시드 정책 확인 결과로 섞어 쓰지 않습니다.

## 새 결과가 생기면 왜 바로 ‘성능이 좋아졌다’고 하지 않나요?

학습 데이터는 모델을 가르치는 데 쓰고, 검증 데이터는 설정을 고르는 데 쓰고, 평가 데이터는 선택한 설정을 마지막에 확인하는 데 씁니다. 지금 추가 탐색은 검증 단계만 반복합니다. 이미 본 평가 점수에 계속 맞추는 것을 피하기 위해 기존 평가값은 동결했습니다.

따라서 ‘설정 교체’는 같은 학습량과 시드로 비교한 **검증 기준 개선**을 뜻합니다. 새로운 데이터에서 성능 향상이 입증됐다는 뜻은 아닙니다. 검증을 오래 반복해도 과적합될 수 있어, 최종 주장은 새 날짜·새 경로에서 확인해야 합니다.

## 현재 독립된 설정 선택 뒤 평가 결과는 무엇인가요?

아래는 11/01의 1차 5분할·3시드 평가값이며, 추가 탐색으로 고쳐 쓰지 않습니다. 이전부터 살펴본 같은 수집 데이터라 완전히 새로운 외부 평가셋은 아닙니다.

| 정책 | 핸드오버 감소 | 핑퐁 감소 | 약전계 비율 변화 (%p) | 평균 신호 손실 (dB) |
|---|---:|---:|---:|---:|
'''+ '\n'.join('| '+' | '.join(r)+' |' for r in results)+f'''

DQN은 핸드오버 {d['ho_reduction']:.1%}, 핑퐁 {d['pp_reduction']:.1%}를 줄였고 평균 신호 손실은 {d['rsrp_loss']:.2f} dB였습니다. 하지만 핸드오버 30% 감소 목표에는 못 미쳤고, **네 목표를 모두 충족한 정책은 아직 없습니다.**

‘약전계’는 신호가 −105 dBm 이하로 약한 상태입니다. 비율 변화의 음수는 약한 신호 행이 줄었다는 뜻입니다. %p는 두 비율의 단순 차이입니다. DQN의 약전계 변화 95% 구간은 {ci['weak_delta_pp'][0]:+.3f}~{ci['weak_delta_pp'][1]:+.3f}%p로, 증가와 감소 가능성을 모두 포함합니다. 평균값이 조금 좋아도 악화 가능성을 배제할 수는 없습니다.

## 이전 정책 탐색 이력

{recent}

## 현재 한계와 다음 단계

- 이웃 셀 정보가 사라지면 실제 망이 고른 셀로 강제 이동합니다. 이 재생 한계 때문에 실망 효과를 그대로 예측할 수 없습니다.
- 약전계 비율은 신선한 관측 행 기준입니다. 실제 약전계 지속 시간이나 연결 끊김 비율과 같지 않습니다.
- CQL은 재생으로 만든 고정 합성 데이터를 사용했습니다. 실제 망의 행동 로그를 학습한 결과가 아닙니다.
- 새 CSV가 생겨도 자동으로 학습에 섞지 않고 독립 평가 후보로 보류합니다. Jev 결제·유료 API 호출은 하지 않습니다.
- 이제 새 공통 비교에서는 라운드마다 전체 9계열을 같은 후보 수로 비교합니다. 완료 전에는 전체 순위를 정하지 않고, 체크포인트에서 재개합니다. 12라운드 연속 기준 이상 개선이 없으면 계산을 쉬고 평가 설계를 점검합니다.

## 어떤 리포트를 보면 되나요?

1. **연구 진행 — 쉽게 읽는 최신 설명**: 이 문서. 목적·현황·결과의 의미를 3시간마다 정리합니다.
2. **핸드오버 정책 심화 연구**: 결과표, 탐색 그래프, 신뢰구간, 학습 설정, 코드 경로와 재현 자료를 담은 상세 문서입니다. 지속 탐색 표는 배치가 끝날 때마다 갱신합니다.
3. **핸드오버 AI 모델 연구 (10/28)**: 예측 모델과 초기 정책 비교를 담은 기존 기록입니다. 후속 실험과 구분해 보존합니다.

기존 모음: C:\\Users\\admin\\NetworkTrackerApp\\viz_build\\index.html → 연구.
원본 페이지 동기화 가능 여부는 마지막 실제 저장 결과를 기준으로 기록합니다. 채팅의 outputs 리포트 링크는 최신본을 가리킵니다.
'''
    # Render deliberately simple, dependency-free HTML from this controlled Markdown.
    body=[];in_list=False;in_table=False
    for line in md.splitlines():
        if line.startswith('|'):
            if '|---' in line:continue
            cells=line.strip('|').split('|')
            if not in_table:body.append('<div class="table"><table>');in_table=True
            body.append('<tr>'+''.join('<td>'+html.escape(v.strip())+'</td>' for v in cells)+'</tr>');continue
        if in_table:body.append('</table></div>');in_table=False
        if line.startswith('- '):
            if not in_list:body.append('<ul>');in_list=True
            body.append('<li>'+html.escape(line[2:])+'</li>');continue
        if in_list:body.append('</ul>');in_list=False
        if line.startswith('# '):body.append('<h1>'+html.escape(line[2:])+'</h1>')
        elif line.startswith('## '):body.append('<h2>'+html.escape(line[3:])+'</h2>')
        elif line.strip():
            pieces=line.split('**');text=''.join(('<strong>' if i%2 else '</strong>')+html.escape(p) if i else html.escape(p) for i,p in enumerate(pieces))
            body.append('<p>'+text+'</p>')
    if in_table:body.append('</table></div>')
    if in_list:body.append('</ul>')
    page='''<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>연구 진행 · 쉽게 읽는 최신 설명</title><style>body{margin:0;background:#f3f6fa;color:#24384e;font:16px/1.95 "Malgun Gothic",system-ui,sans-serif}main{max-width:860px;margin:32px auto;padding:40px;background:#fff;border:1px solid #dce4ef;border-radius:12px}h1{font-size:32px;line-height:1.4;letter-spacing:-.04em;border-top:4px solid #215fd1;padding-top:22px}h2{font-size:22px;margin-top:38px;padding-top:20px;border-top:1px solid #dce4ef}p,li{color:#52677f}strong{color:#172b43}a{color:#215fd1}.table{overflow:auto}table{border-collapse:collapse;width:100%;font-size:13px;white-space:nowrap}td{padding:11px;border-bottom:1px solid #dce4ef}tr:first-child{background:#edf3fc;font-weight:bold}.links{display:flex;gap:18px;flex-wrap:wrap;font-size:14px}@media(max-width:650px){main{padding:22px 18px;margin:12px}h1{font-size:25px}body{font-size:15px}}@media print{body{background:white}main{border:0;margin:0;padding:0}}</style><main><div class="links"><a href="핸드오버_정책_심화연구.html">상세 연구 리포트 →</a></div>'''+''.join(body)+'</main></html>'
    page=page.replace('상세 연구 리포트 →</a></div>','상세 연구 리포트 →</a><a href="연구_4단계_진행점검.html">연구 4단계 진행 점검 →</a><a href="전체모델_정책비교.html">전체 모델·정책 결합 비교 →</a></div>')
    from research_stage_report import generate
    stage=generate(when,archive)
    save(OUT/'연구_진행_쉬운설명.md',md);save(OUT/'research_progress.html',page)
    archived=page.replace('href="핸드오버_정책_심화연구.html"','href="../핸드오버_정책_심화연구.html"').replace('href="연구_4단계_진행점검.html"',f'href="four_stages_{archive}.html"').replace('href="전체모델_정책비교.html"','href="../전체모델_정책비교.html"')
    save(OUT/'research_reports'/f'{archive}.md',md);save(OUT/'research_reports'/f'{archive}.html',archived)
    mirror=True
    try:save(ROOT/'viz_build/research_progress.html',page.replace('핸드오버_정책_심화연구.html','policy_research.html').replace('연구_4단계_진행점검.html','research_stages.html'))
    except PermissionError:mirror=False
    from sync_research_hub import sync
    hub=sync()
    save(marker,json.dumps(dict(timestamp=now.timestamp(),time=when,last_batch=state.get('last_batch',state['completed_batches']),archive=archive,mirrored=mirror,inclusive_last_round=inclusive['completed_rounds'] if inclusive else 0,**stage),ensure_ascii=False,indent=2))
    print(json.dumps(dict(status='UPDATED',time=when,legacy_new_batches=len(new),new_inclusive_rounds=len(since),inclusive_screening_configurations=sum(r['screening_configurations'] for r in since),inclusive_confirmation_configurations=sum(r['confirmation_configurations'] for r in since),inclusive_weight_updates=sum(r['updates'] for r in since),mirrored=mirror,**hub),ensure_ascii=False))

if __name__=='__main__':main()
