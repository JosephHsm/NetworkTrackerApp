"""Evidence-based four-stage research progress report; refreshed with each brief."""
import html,json,re
from datetime import datetime,timezone,timedelta
from pathlib import Path

from research_paths import ROOT, EXPORT as OUT
def read(p):return json.loads(p.read_text(encoding='utf-8'))
def inline(s):
    s=html.escape(s)
    s=re.sub(r'\*\*(.*?)\*\*',r'<strong>\1</strong>',s)
    return re.sub(r'\[([^\]]+)\]\((https://[^ )]+|[^(): ]+\.html)\)',r'<a href="\2">\1</a>',s)
def render(md):
    result=[];listing=False;tab=False;first=False
    for line in md.splitlines():
        if line.startswith('|'):
            if re.match(r'^\|[- :|]+$',line):continue
            if not tab:result.append('<div class="table"><table>');tab=True;first=True
            tag='th' if first else 'td';first=False
            result.append('<tr>'+''.join(f'<{tag}>{inline(c.strip())}</{tag}>' for c in line.strip('|').split('|'))+'</tr>');continue
        if tab:result.append('</table></div>');tab=False
        if line.startswith('- '):
            if not listing:result.append('<ul>');listing=True
            result.append('<li>'+inline(line[2:])+'</li>');continue
        if listing:result.append('</ul>');listing=False
        if line.startswith('### '):result.append('<h3>'+inline(line[4:])+'</h3>')
        elif line.startswith('## '):result.append('<h2>'+inline(line[3:])+'</h2>')
        elif line.startswith('# '):result.append('<h1>'+inline(line[2:])+'</h1>')
        elif line.strip():result.append('<p>'+inline(line)+'</p>')
    if tab:result.append('</table></div>')
    if listing:result.append('</ul>')
    return ''.join(result)

def generate(when=None,archive=None):
    now=datetime.now(timezone(timedelta(hours=9)))
    when=when or now.strftime('%Y-%m-%d %H:%M KST');archive=archive or now.strftime('%Y%m%d_%H%M%S')
    state=read(OUT/'policy_continuous/state.json')
    manifest=read(ROOT/'analysis_output/policy_v2/manifest.json')
    summary=read(ROOT/'analysis_output/policy_v2/summary.json')['policies']
    rows=[]
    for k in ('A3','Rule','Q','DQN','DQN+GRU','CQL','PPO'):
        d=summary[k]['delta'];ci=summary[k]['ci95']
        rows.append(f'| {k} | {d["ho_reduction"]:.1%} | {d["pp_reduction"]:.1%} | {d["weak_delta_pp"]:+.3f} | {d["rsrp_loss"]:+.2f} |')
    history=state['history'];screen=sum(h['trials'] for h in history)
    confirmation=sum(h.get('confirmation_trials',0) for h in history)
    d=summary['DQN']['delta'];ci=summary['DQN']['ci95']
    latest=history[-1]
    inclusive_path=OUT/'all_model_policy/state.json'
    inclusive=read(inclusive_path) if inclusive_path.exists() else None
    inclusive_note='새 전체 모델 공통 비교는 아직 시작하지 않았습니다.'
    if inclusive:
        n=inclusive['completed_rounds']
        inclusive_note=f'새 전체 모델 공통 비교는 완료 라운드 {n}개입니다. XGBoost·RNN·LSTM·GRU를 같은 DQN에 결합하고 Q·DQN·PPO·CQL·Rule도 포함합니다. 각 모델은 동일한 후보 수·확인 시드와 자기 검증 결과로 설정을 탐색하며, 첫 공통 설정의 예측기 교체 비교와 각자 튜닝한 표를 나눴습니다.'
        if inclusive['history']:
            h=inclusive['history'][-1]
            inclusive_note+=f' 마지막 완료 라운드: 선별 설정 {h["screening_configurations"]}개·확인 설정 {h["confirmation_configurations"]}개·정책 학습 {h["policy_fit_count"]}회, 합산 {h["seconds"]/60:.1f}분입니다.'
    md=f'''# 研究進捗_PLACEHOLDER

작성 기준: {when} · 완료 배치 {state['completed_batches']}개 · 정량적 진행률을 임의로 부여하지 않았습니다.

## 현재 위치를 먼저 말하면

**① 연구 질문·평가 기준은 정리됨 → ② 내부 비교 실험은 수행했고 추가 탐색 중 → ③ 차이의 원인 분석은 부분 진행 → ④ 새 데이터에서의 검증은 아직 미실시**입니다.

전체 방향은 AI 연구의 실험 절차와 맞습니다. 현재 결과의 범위는 실측 로그 재생 환경에서의 내부 비교와 검증 후보 탐색입니다. 논문에서 주장할 연구 기여, 결과의 원인과 외부 재현성은 더 확인해야 합니다.

최신 확장: **{inclusive_note}** 새 비교도 기존과 같은 수집 데이터의 반복 검증이므로 4단계의 새 데이터 평가는 여전히 미실시입니다.

| 연구 단계 | 현재 판단 | 확인된 근거 | 아직 부족한 것 | 다음 완료 조건 |
|---|---|---|---|---|
| 1. 질문과 가설 | 질문·목표 정리, 가설 정교화 진행 | RQ1~3, 핸드오버·핑퐁·신호 보호 기준 | 핵심 기여, 주가설·주평가지표 확정 | 기존 연구와의 차이 및 반박 가능한 가설을 문서로 고정 |
| 2. 비교 실험 설계·수행 | 내부 실험 수행, 반복 탐색 진행 | 세션 분리, 여러 기준선·모델, 보상 탐색, 5시드 확인 | 모델별 탐색 예산 공정성, 모든 특징의 의사결정 시점 점검 | 비교 예산·정보 이용 시점·통계 분석 계획 고정 |
| 3. 왜 차이가 나는지 분석 | 부분 진행 | 입력 중요도·정책 표·이동수단·시드별 결과 | 구성 요소 제거 실험, 강제 이동·동결 처리 민감도 | 한 요소만 바꾼 비교로 개선 원인과 실패 조건 설명 |
| 4. 새 데이터에서도 유지되는지 | 미실시 | 기존 31개 세션 안의 분리 평가만 있음 | 새 날짜·경로의 외부 평가, 실망 효과 확인 | 설정 고정 후 별도 데이터에서 평가하고 적용 범위를 명시 |

## 1단계 · 질문과 가설을 정한다

### 지금까지 한 일

- 연구 목적은 신호 품질을 지키면서 불필요한 핸드오버와 30초 내 복귀인 핑퐁을 줄이는 것입니다.
- 기존 문서의 RQ1은 핑퐁·손해 핸드오버 예측, RQ2는 5·10·15초 뒤 신호 예측, RQ3은 허용/보류 정책의 효과와 모델 비교입니다.
- 현재 정책 실험의 설계 목표는 핸드오버 30% 이상 감소, 핑퐁 80% 이상 감소, 약전계 관측 행 비율 증가 없음, 평균 RSRP 손실 1 dB 이내입니다. 이 문턱은 연구 설계의 선택이며 통계적 인증 기준은 아닙니다.

### 남은 일

어떤 결과가 연구의 핵심 기여인지 더 좁혀야 합니다. 예를 들어 실측 로그의 관측 누락 때문에 정책 효과 추정이 얼마나 달라지는지, 신호 보호 조건 아래 어떤 상황에서 학습 정책이 단순 규칙을 이기는지를 구체적으로 물을 수 있습니다. 기존 모델을 구현해 나열했다는 사실만으로 차별점이 정해지는 것은 아닙니다.

후속 실험에 고정할 주가설의 예시는 **“같은 정보와 비교 예산에서 학습 정책이 검증으로 고른 규칙보다 신호 보호 조건을 만족하면서 핑퐁을 더 줄인다”**입니다. 이 문장은 후속 설계를 위한 제안이며 과거 실험 전에 사전 등록했다고 소급해 주장하지 않습니다. 개선이 없거나 특정 이동수단에서만 나타나는 결과도 반증·적용 조건으로 보고해야 합니다.

### 이 단계의 완료 조건

핵심 질문 하나, 주평가지표와 신호 제약, 비교 기준, 실패 시 결론, 관련 연구와의 차이를 같은 문서에 고정하는 것입니다. 현재 질문 정의는 있으나 이 전체가 확정된 상태는 아닙니다.

## 2단계 · 비교 실험을 설계하고 실행한다

### 지금까지 한 일

- 정책 실험은 현재 {len(manifest['data'])}개 세션을 사용합니다. 동일 세션이 같은 분할의 학습·검증·평가에 동시에 들어가지 않게 분리했습니다.
- 초기 정책 연구는 바깥 5분할과 내부 검증 분리를 적용하고, 학습 정책을 최종 3시드로 평가했습니다. 초기 탐색 후보 평가는 345개입니다.
- 실제 망 재생, 고정 A3 규칙, 검증으로 고른 규칙, 표 Q-learning, DQN, DQN+GRU, CQL, PPO를 비교했습니다. 예측 모델 비교는 10/28의 별도 연구 기록에 있습니다.
- 지속 탐색은 완료 배치 {state['completed_batches']}개, 선별 후보 평가 {screen}개, 확인 설정 평가 {confirmation}개입니다. 확인 설정 2개가 기존·후보 한 쌍입니다. 반복된 기준 설정도 평가 횟수에 포함되며 서로 다른 가중치 {screen}개를 발견했다는 뜻은 아닙니다.
- 현재는 2시드로 후보를 선별한 뒤, 후보를 동결하고 기존 설정과 별도의 새 5시드로 확인합니다. 평균 개선 기준과 3/5 이상 시드 승리를 만족할 때만 설정을 바꿉니다. 이것은 후보 선택 규칙이며 유의성 검정이 아닙니다.
- 마지막 완료 배치 {latest['batch']}는 Q-learning + {latest['neural_family']}, 실행 {latest['seconds']/60:.1f}분, 설정 교체 {latest['updates']}곳입니다. 이는 검증 단계의 선택이며 외부 성능 향상 확정이 아닙니다.

### 남은 일

- 반복 검증도 검증셋에 과적합될 수 있습니다. 최종 평가를 동결한 것과 새 데이터 일반화가 입증된 것은 다릅니다.
- 모델마다 탐색 후보 수와 누적 탐색 횟수가 다릅니다. 특히 Q는 매번 실행하고 다른 정책은 순환하므로 총 탐색 예산이 같지 않습니다. 모델 자체의 우열을 주장하려면 예산을 맞춘 비교가 필요합니다.
- 기존 policy_v2에는 XGBoost·RNN·LSTM 결합 비교가 빠졌고 Q 중심 보상 후보 선택의 제한이 있었습니다. 새 전체 모델 실험은 예측기 4종 결합과 모델별 독립 탐색으로 이 범위를 확장했습니다. 후보 수·시드는 맞췄지만 예측기 추가 학습, CQL/PPO의 데이터 이용 방식과 실제 계산량까지 같게 한 것은 아닙니다. 시간·학습 재생 행 수를 함께 기록합니다.
- 일부 초기 지도학습 특징에는 핸드오버 직후 신호가 들어갑니다. 사후 진단에는 사용할 수 있지만 핸드오버 전 차단 성능을 주장하려면 그 시점에 알 수 없는 입력을 제외해야 합니다.
- 초기 분류의 핑퐁 326건과 정책 재생의 353건은 집계·셀 식별 정의가 다릅니다. 논문에서는 문제별 정의를 명확히 쓰고 서로 같은 수치처럼 섞지 않아야 합니다.
- 약전계는 신선한 행 비율입니다. 샘플 간격이 불규칙하므로 약전계 지속 시간이나 실제 연결 끊김과 동일하게 해석할 수 없습니다.

### 현재 동결된 평가가 말하는 것

아래는 새 5시드 확인 결과와 구분한 11/01 초기 정책 평가 평균입니다.

| 정책 | HO 감소 | 핑퐁 감소 | 약전계 변화 (%p) | 평균 RSRP 손실 (dB) |
|---|---:|---:|---:|---:|
'''+ '\n'.join(rows)+f'''

DQN은 HO {d['ho_reduction']:.1%}, 핑퐁 {d['pp_reduction']:.1%} 감소, 평균 신호 손실 {d['rsrp_loss']:.2f} dB입니다. 네 목표를 모두 충족한 정책은 아직 없습니다. DQN의 약전계 변화 95% 구간은 {ci['weak_delta_pp'][0]:+.3f}~{ci['weak_delta_pp'][1]:+.3f}%p로 악화 가능성을 배제하지 못합니다. 기존에 살펴본 같은 수집 데이터 안의 평가이므로 새로운 외부 평가로 부르지 않습니다.

### 이 단계의 완료 조건

모델별 정보·탐색 예산·시간 기준·평가 지표와 불확실성 계산을 고정하고, 같은 설계로 재현 가능한 비교표를 만드는 것입니다. 그 기반은 있지만 모든 공정성·측정 조건을 확정한 것은 아닙니다.

## 3단계 · 왜 차이가 났는지 분석한다

### 지금까지 한 일

초기 입력 중요도와 Q 정책 표로 어떤 신호·복귀 상태에서 보류하는지 관찰했고, 이동수단별 성능과 학습 시드별 편차를 기록했습니다. GRU를 넣은 정책과 넣지 않은 정책도 비교했습니다.

이는 개선 원인의 후보를 찾은 단계입니다. GRU 유무와 함께 선택된 보상 설정 등도 달라질 수 있으므로, 그 결과만으로 “GRU 때문에 개선됐다”는 결론을 확정할 수 없습니다. 시드를 늘리는 일은 학습 변동성 확인이며 어떤 구성 요소가 원인인지 직접 증명하지는 않습니다.

새 전체 모델 실험에서는 이 문제를 줄이기 위해 첫 라운드의 공통 보상·정책 학습률·DQN 크기·시드를 고정하고 예측기만 바꾼 anchor 표를 따로 만듭니다. 이는 이 설정·재생 환경 아래의 1차 구성 요소 비교이며, 보상 항목 제거·재생 가정 민감도와 실망 효과 검증을 모두 마친 것은 아닙니다.

### 필요한 후속 실험

- 보상 항목을 하나씩 제거하거나 변경해, 핑퐁·약전계·신호 차이 비용이 각 지표에 주는 영향을 비교합니다.
- 다른 조건을 같게 두고 GRU 입력만 넣고 빼서 예측 정보의 순수한 기여를 확인합니다.
- 가상 셀이 관측 목록에서 사라질 때의 강제 이동 처리와 관측 동결 기준을 바꿔 결론이 유지되는지 확인합니다.
- 차량·지하철·도보, 약전계·강전계처럼 조건을 나눠 개선과 악화 사례를 직접 설명합니다.
- 반응과 처리량·지연을 관측하지 못한 부분은 결과의 적용 한계로 남깁니다.

### 이 단계의 완료 조건

다른 조건은 고정하고 한 요소만 바꾸는 비교로 효과의 원인을 분리하고, 실패하는 조건까지 설명하는 것입니다. 현재 이 단계는 부분 진행입니다.

## 4단계 · 새 데이터에서도 유지되는지 확인한다

### 현재 상태

**새 날짜·새 경로를 독립 평가로 사용한 결과는 아직 없습니다.** 같은 기존 로그를 다시 나누거나 시드만 바꾸는 것은 새 데이터를 확보한 것과 다릅니다. 현재 발견해 보류 중인 추가 CSV는 {len(state.get('reserved_new_sessions',[]))}개입니다.

실제 망의 반응도 검증하지 않았습니다. 정책이 보류했을 때 망의 부하·간섭·이웃 목록이 어떻게 달라졌을지는 원래 로그에 기록되어 있지 않습니다. 단순 재생 결과를 실망 효과와 동일시할 수 없습니다.

### 다음에 할 일과 완료 조건

- 새 날짜·경로 데이터를 평가용으로 따로 정하고, 보기 전에 후보와 전처리·지표·판정 기준을 고정합니다.
- 이 데이터를 이용해 가중치를 다시 고르지 않고 평가합니다. 결과를 보고 다시 조정했다면 이후 확인에는 또 다른 독립 데이터가 필요합니다.
- 가능하면 경로·날짜 그룹, 이동수단, 다른 단말 등으로 적용 범위를 넓힙니다.
- 새 로그에서도 재생 평가의 한계는 남습니다. 실제 망 개선 주장에는 검증된 시뮬레이터나 망 측 시험 등 주장 범위에 맞는 별도 근거가 필요합니다.

## 지금의 우선순위

자동 가중치 탐색은 보조 작업으로 유지하되, 다음 주요 연구 작업은 **주가설과 비교 예산 고정 → 구성 요소·재생 가정의 민감도 분석 → 새 데이터 평가 설계**입니다. 추가 실행 횟수와 학습 시간이 연구 완성도를 대신하지 않습니다.

## 근거와 연결 문서

- 초기 질문·연구 방향: C:\\Users\\admin\\NetworkTrackerApp\\핸드오버_AI_연구방향.md 및 viz_build/model_research.html의 RQ1~3·라벨 정의·한계.
- 정책 설계·평가: analysis_output/policy_v2/manifest.json, summary.json, search.json, records.json.
- 추가 탐색: outputs/policy_continuous/state.json, batch_*/plan.json, trials.json, confirmations.json, comparisons.json, verification.json.
- [상세 수치와 탐색 그래프](핸드오버_정책_심화연구.html) · [쉽게 읽는 최신 진행 문서](research_progress.html).
- [전체 모델·정책 결합의 공통 비교와 모델별 튜닝](전체모델_정책비교.html).
- [scikit-learn: 설정 선택과 평가의 분리](https://scikit-learn.org/stable/auto_examples/model_selection/plot_nested_cross_validation_iris.html).
- [NeurIPS: 주장·실험·재현성·불확실성 보고](https://neurips.cc/public/guides/PaperChecklist).
- [Google Research: 강화학습 평가의 불확실성](https://research.google/blog/rliable-towards-reliable-evaluation-reporting-in-reinforcement-learning/).

문서 해석: ‘완료’는 적어 둔 범위의 작업 상태이며 논문 심사 통과를 뜻하지 않습니다. 현재 상태를 근거 없이 진행률 퍼센트로 환산하지 않았습니다.
'''
    md=md.replace('研究進捗_PLACEHOLDER','AI 연구 4단계 진행 점검 — 현재 위치와 남은 검증')
    css='''body{margin:0;background:#f3f6fb;color:#23394f;font:15px/1.95 "Malgun Gothic",system-ui,sans-serif}main{max-width:1020px;margin:32px auto;padding:40px;background:white;border:1px solid #dce4ef;border-radius:12px}h1{font-size:32px;line-height:1.4;letter-spacing:-.04em;border-top:4px solid #215fd1;padding-top:24px}h2{font-size:24px;margin-top:40px;padding-top:24px;border-top:1px solid #dce4ef}h3{font-size:17px;margin:26px 0 10px}p,li{color:#52677e}strong{color:#193f73}a{color:#215fd1}.table{overflow-x:auto;margin:22px 0}table{border-collapse:collapse;min-width:700px;width:100%;font-size:13px;line-height:1.8}td,th{padding:12px 13px;border-bottom:1px solid #dce4ef;text-align:left;vertical-align:top}th{background:#edf3fc;color:#203e68}.links{display:flex;gap:20px;flex-wrap:wrap;font-size:13px}.tag{background:#edf3fc;border-radius:5px;padding:6px 12px;color:#215fd1;font-size:12px}@media(max-width:700px){main{padding:22px 17px;margin:12px}h1{font-size:25px}h2{font-size:21px}}@media print{body{background:white}main{border:0;padding:0;margin:0}h2,h3{break-after:avoid}table{min-width:0;font-size:10px}}'''
    page='<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>AI 연구 4단계 진행 점검</title><style>'+css+'</style></head><body><main><div class="links"><a href="research_progress.html">← 쉽게 읽는 연구 진행</a><a href="핸드오버_정책_심화연구.html">상세 연구 리포트 →</a></div><p class="tag">질문·가설 / 비교 설계 / 원인 분석 / 새 데이터 검증</p>'+render(md)+'</main></body></html>'
    OUT.mkdir(parents=True,exist_ok=True);(OUT/'research_reports').mkdir(exist_ok=True)
    (OUT/'연구_4단계_진행점검.md').write_text(md,encoding='utf-8')
    (OUT/'연구_4단계_진행점검.html').write_text(page,encoding='utf-8')
    (OUT/'research_reports'/f'four_stages_{archive}.md').write_text(md,encoding='utf-8')
    archived=page.replace('href="research_progress.html"',f'href="{archive}.html"').replace('href="핸드오버_정책_심화연구.html"','href="../핸드오버_정책_심화연구.html"').replace('href="전체모델_정책비교.html"','href="../전체모델_정책비교.html"')
    (OUT/'research_reports'/f'four_stages_{archive}.html').write_text(archived,encoding='utf-8')
    try:
        (ROOT/'viz_build/research_stages.html').write_text(page.replace('핸드오버_정책_심화연구.html','policy_research.html'),encoding='utf-8');mirrored=True
    except PermissionError:mirrored=False
    return dict(stage_report_updated=when,completed_batches=state['completed_batches'],stage_report_mirrored=mirrored)

if __name__=='__main__':print(json.dumps(generate(),ensure_ascii=False))
