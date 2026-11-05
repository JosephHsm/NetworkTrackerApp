"""Standalone report for the inclusive, validation-only model-policy study."""
import html,json,statistics
from datetime import datetime,timezone,timedelta
from pathlib import Path
from research_paths import EXPORT as OUT

ROOT=Path(__file__).resolve().parents[2]
ORDER=('Q','DQN','PPO','CQL','DQN+XGBoost','DQN+RNN','DQN+LSTM','DQN+GRU','Rule')
def read(p):return json.loads(p.read_text(encoding='utf-8'))
def records(folder):
    found=[]
    for p in sorted(folder.glob('fold*.json')):
        r=read(p)
        if isinstance(r,dict) and 'selected' in r and 'screening' in r:found.append(r)
    return found
def table(head,rows):
    return '<div class="table"><table><thead><tr>'+''.join('<th>'+html.escape(h)+'</th>' for h in head)+'</tr></thead><tbody>'+''.join('<tr>'+''.join('<td>'+str(v)+'</td>' for v in row)+'</tr>' for row in rows)+'</tbody></table></div>'
def mean(vals):return statistics.mean(vals) if vals else 0.
def metrics(rows,selected=True):
    trials=[r['selected'] if selected else next(c for c in r['confirmation'] if c['role']=='anchor') for r in rows]
    keys=('ho_reduction','pp_reduction','weak_delta_pp','rsrp_loss')
    d={key:mean([r['delta'][key] for r in trials]) for key in keys}
    forecasts=[s['forecast']['validation_mae'] for r in trials for s in r['seed_results'] if s.get('forecast')]
    d['forecast_mae']=mean(forecasts) if forecasts else None
    return d

def generate(store):
    store=Path(store);state=read(store/'state.json');rid=state.get('current_round') or state['completed_rounds']
    if not rid:return
    folder=store/f'round_{rid:04d}';plan=read(folder/'plan.json');all_rows=records(folder)
    done=(folder/'complete.json').exists();when=datetime.now(timezone(timedelta(hours=9))).strftime('%Y-%m-%d %H:%M KST')
    total=len(plan['families'])*(1 if plan['smoke'] else 5)
    progress=f'라운드 {rid} · {len(all_rows)}/{total}개 분할·모델 조합 완료'
    display_round=rid if done else state['completed_rounds']
    result_rows=all_rows if done else (records(store/f'round_{display_round:04d}') if display_round else [])
    rows=[]
    if result_rows:
        for family in ORDER:
            rr=[r for r in result_rows if r['family']==family]
            if not rr:continue
            d=metrics(rr);elapsed=sum(r['seconds'] for r in rr)
            rows.append([html.escape(family),str(len(rr)),f'{d["ho_reduction"]:.1%}',f'{d["pp_reduction"]:.1%}',f'{d["weak_delta_pp"]:+.3f}',f'{d["rsrp_loss"]:+.2f}',
                '—' if d['forecast_mae'] is None else f'{d["forecast_mae"]:.3f}',f'{elapsed/60:.1f}분'])
    main_table=(f'<p><b>마지막 완료 라운드 {display_round}의 결과입니다.</b> 진행 중인 라운드의 일부 조합은 이 표에 합산하지 않습니다.</p>'+table(['정책 / 예측 결합','검증 분할 수','HO 감소','핑퐁 감소','약전계 변화 (%p)','평균 RSRP 손실 (dB)','예측 MAE (dB)','선별+확인 시간'],rows)) if result_rows else '<div class="note">아직 전체 라운드 완료 결과가 없습니다. 진행 중 일부 조합으로 전체 모델 순위를 만들지 않습니다.</div>'
    first=store/'round_0001';controlled=[]
    if (first/'complete.json').exists():
        first_rows=records(first)
        for family in ('DQN','DQN+XGBoost','DQN+RNN','DQN+LSTM','DQN+GRU'):
            rr=[r for r in first_rows if r['family']==family]
            if not rr:continue
            d=metrics(rr,selected=False)
            controlled.append([family,f'{d["ho_reduction"]:.1%}',f'{d["pp_reduction"]:.1%}',f'{d["weak_delta_pp"]:+.3f}',f'{d["rsrp_loss"]:+.2f}',
                '입력 추가 없음' if d['forecast_mae'] is None else f'{d["forecast_mae"]:.3f}'])
    ablation=table(['첫 라운드 공통 설정','HO 감소','핑퐁 감소','약전계 변화 (%p)','RSRP 손실 (dB)','예측 MAE (dB)'],controlled) if controlled else '<p>첫 전체 라운드 완료 후, 같은 보상·정책 학습률·DQN 크기·확인 시드에서 예측기만 바꾼 표를 작성합니다.</p>'
    decisions=[];settings=[]
    for r in all_rows:
        d=r['decision'];a,c=r['confirmation']
        decisions.append([str(r['fold']+1),r['family'],f'{d["seed_wins"]}/{d["seed_count"]}',str(d['mean_improved']),
            '교체' if d['accepted'] else '기존 유지',f'{a["delta"]["ho_reduction"]:.1%} → {c["delta"]["ho_reduction"]:.1%}',f'{a["delta"]["weak_delta_pp"]:+.3f} → {c["delta"]["weak_delta_pp"]:+.3f}'])
        settings.append([str(r['fold']+1),r['family'],'<code>'+html.escape(json.dumps(r['selected']['config'],ensure_ascii=False))+'</code>'])
    decision_table=table(['분할','모델','신규 후보 승수','평균 기준 개선','결정','HO 감소 기존→후보','약전계 변화 기존→후보 (%p)'],decisions)
    setting_table=table(['분할','모델','선택한 설정'],settings)
    variable_table=table(['변수','기본 / 탐색 범위','의미'],[
        ['보상 비용','HO=1 고정, 핑퐁 0.25~81·약전계 0.5~45·신호 차이 0.005~1.5','모델이 어떤 손해를 더 피하도록 할지'],
        ['정책 학습률','0.0001 / 0.0003 / 0.0005 / 0.001','DQN·PPO·CQL이 학습 중 가중치를 얼마나씩 바꿀지'],
        ['Q 갱신률','0.03 / 0.05 / 0.1 / 0.3','표 Q 값을 한 번에 얼마나 바꿀지'],
        ['CQL 보수 계수','0.03 / 0.1 / 0.3 / 1','고정 합성 데이터에서 보지 못한 행동의 Q값 억제'],
        ['RNN·LSTM·GRU 예측기','은닉 16 / 32, 학습률 0.001 / 0.002 / 0.003','예측기 구조와 학습 설정'],
        ['XGBoost 예측기','깊이 2 / 3 / 4, 트리 80 / 120 / 160, 학습률 0.03 / 0.05 / 0.1','트리 복잡도와 학습 설정'],
        ['고정한 것',f'예측 입력 12행×2특징, 10초 뒤 변화량, DQN 은닉 64×64, γ=0.99, 정책 {plan["policy_epochs"]} epoch·시계열 예측 {plan["forecast_epochs"]} epoch','한 라운드 안에서 임의로 바꾸지 않음'],
        ['시드',f'선별 {plan["screening_seeds"]}, 확인 {plan["confirmation_seeds"]}','좋은 시드를 선택하지 않고 같은 시드끼리 비교·평균. 확인 시드는 라운드마다 새로 정함']])
    css='''body{margin:0;background:#f3f6fa;color:#21364e;font:15px/1.9 "Malgun Gothic",system-ui,sans-serif}main{max-width:1180px;margin:28px auto;padding:36px;background:white;border:1px solid #dce4ef;border-radius:12px}h1{font-size:33px;line-height:1.4;letter-spacing:-.04em;margin:18px 0}h2{font-size:24px;line-height:1.5;margin-top:40px;padding-top:22px;border-top:1px solid #dce4ef}h3{font-size:18px}p,li{color:#53697f}strong{color:#193f73}a{color:#215fd1}.note{padding:18px 22px;background:#edf3ff;border-left:4px solid #215fd1;border-radius:5px;margin:20px 0}.table{overflow-x:auto;margin:18px 0}table{border-collapse:collapse;width:100%;font-size:12px;line-height:1.8}td,th{padding:11px;border-bottom:1px solid #dce4ef;text-align:left;vertical-align:top}th{background:#edf3fc;white-space:nowrap}code{font:11px Consolas,monospace;overflow-wrap:anywhere}.links{display:flex;gap:20px;flex-wrap:wrap;font-size:13px}details{padding:15px 0;border-top:1px solid #dce4ef;margin-top:20px}summary{cursor:pointer;font-weight:bold}.kicker{font:12px Consolas,monospace;letter-spacing:.1em;color:#215fd1}@media(max-width:720px){main{margin:12px;padding:20px 16px}h1{font-size:26px}h2{font-size:22px}}@media print{main{margin:0;padding:0;border:0}body{background:white}h2,h3{break-after:avoid}}
    '''
    page=f'''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>전체 모델·정책 결합 비교</title><style>{css}</style></head><body><main><div class="links"><a href="보고서_모음.html">← 보고서 모음</a><a href="research_progress.html">쉽게 읽는 진행 설명</a><a href="핸드오버_정책_심화연구.html">이전 정책 실험 기록</a></div><p class="kicker">INCLUSIVE MODEL / POLICY STUDY</p><h1>예측기를 바꾼 효과와<br>각 모델을 튜닝한 결과를 나눠 봅니다.</h1><p>갱신: {when} · {html.escape(progress)}</p><div class="note"><b>새로운 공통 비교 설계입니다.</b> Q·DQN·PPO·CQL, DQN+XGBoost·RNN·LSTM·GRU, 규칙을 포함합니다. 모든 모델은 같은 학습/검증 세션, 후보 수와 확인 시드를 씁니다. Q의 우수 설정을 다른 모델의 후보로 가져오지 않습니다. 새 데이터에 대한 최종 평가가 아니라 반복 검증 탐색입니다.</div>
    <h2>1. 무엇을 바꾸고 무엇을 고정하나요?</h2>{variable_table}<p>각 라운드의 후보는 파라미터 조합 일부를 샘플링합니다. 위 범위의 모든 조합을 전수 탐색하거나 전역 최적값을 보장하지 않습니다. 최초 라운드는 공통 정책 설정으로 시작하고 이후에는 각 모델 자신의 검증 결과를 기준으로 탐색합니다.</p>
    <h2>2. 각 모델이 설정을 탐색한 뒤의 결과</h2>{main_table}<div class="note"><b>약전계 변화란?</b> 이번 연구에서 RSRP ≤−105 dBm인 신선한 관측 행의 비율을 약전계 비율로 둡니다. 변화 = 모델의 약전계 비율 − 실제 망의 약전계 비율이며 단위는 %p입니다. 예를 들어 2%에서 1.5%가 되면 −0.5%p입니다. 음수는 약한 신호 관측이 감소, 양수는 증가입니다. 관측 행 기준이므로 실제 약전계 지속 시간이나 연결 끊김 비율과 같지 않습니다.</div><p>분할별 확인 5시드의 평균을 구한 뒤 검증 분할별 변화율을 평균합니다. 검증 세션은 분할 사이에 겹칠 수 있으므로 이 표는 독립 외부 테스트 합계나 새 95% 신뢰구간이 아닙니다. 예측 MAE와 정책의 감소율은 다른 목표이며 좋은 예측 오차가 반드시 좋은 정책 결과로 이어지지는 않습니다.</p>
    <h2>3. 예측기만 바꾼 공통 설정 비교</h2>{ablation}<p>최초 라운드의 기존 설정(anchor)은 보상 [3,5,0.1], 정책 학습률 0.0003, 같은 DQN 크기와 학습량을 고정합니다. 예측기는 같은 관측 행·목표·분할을 사용하고 종류를 바꿉니다. 이 표는 각자 튜닝한 최고점 표와 구분합니다. 시계열 예측기는 동일한 기본 은닉 16을 사용하며 XGBoost는 별도 구조이므로 파라미터 수까지 같다는 뜻은 아닙니다.</p>
    <h2>4. 동일하게 둔 비교 예산과 남은 차이</h2><ul><li>모델별·분할별 후보 {plan['candidates_per_family']}개를 선별 시드 {len(plan['screening_seeds'])}개로 평가하고, 가장 좋은 신규 후보를 동결한 뒤 기존 설정과 별도 시드 {len(plan['confirmation_seeds'])}개로 다시 비교합니다.</li><li>선택 기준은 약전계 비율 증가 없음과 평균 RSRP 손실 ≤1 dB를 우선하고, 그 안에서 HO·핑퐁 감소의 동일 가중 평균을 봅니다. 평균 기준 개선 0.002 초과와 시드별 과반 승리가 있어야 교체합니다. 유의성 검정 결과는 아닙니다.</li><li>후보 수와 학습 epoch가 같아도 CPU 시간·데이터 이용량은 다릅니다. 예측 결합은 예측기 학습이 추가되고, CQL은 고정 합성 버퍼를 쓰며, PPO는 새 행동을 샘플링합니다. 실행 시간과 학습 재생 행 수를 원자료에 기록하므로 모든 계산량을 완전히 같게 했다고 주장하지 않습니다.</li><li>첫 공통 비교는 정해진 짧은 학습 예산의 비교입니다. 모든 모델이 수렴했거나 각 모델에 충분한 학습량이라고 확정하지 않았습니다. 이후 학습량 민감도도 별도 과제입니다.</li></ul>
    <h2>5. 확인 결과와 선택한 설정</h2><details open><summary>후보가 몇 개 시드에서 나았고 채택했는지</summary>{decision_table}</details><details><summary>분할·모델별 선택 설정</summary>{setting_table}</details>
    <h2>6. 해석 범위와 근거</h2><ul><li>새로 구성한 XGBoost·RNN·LSTM·GRU는 관측 서빙 신호의 10초 뒤 변화량을 예측합니다. 가상 정책이 유지한 셀의 미래를 직접 예측한 것은 아닙니다.</li><li>예측 입력은 현재까지의 RSRP와 관측 동결 마스크입니다. 미래 값으로 앞부분을 채우지 않으며, 예측기 학습에는 해당 분할의 학습 세션만 사용합니다.</li><li>기존 policy_v2의 외부 평가 세션은 여기서 평가하지 않고 수치를 고쳐 쓰지 않습니다. 재생 가정·강제 이동·샘플 간격·동일 단말/경로의 상관이라는 한계는 계속 남습니다.</li><li>검증 개선과 시드 반복은 새로운 날짜·경로의 일반화 성능을 입증하지 않습니다. 별도 데이터와 고정된 최종 설계가 필요합니다.</li><li>설정·분할·시드·모델 파일: outputs/all_model_policy/round_XXXX/plan.json, fold*_screening.json, fold*_frozen.json, fold*_confirmation.json, fold*.json. 예측기 캐시는 학습/검증 목록·설정·시드별로 분리했습니다.</li></ul><p>방법 참고: <a href="https://scikit-learn.org/stable/auto_examples/model_selection/plot_nested_cross_validation_iris.html">설정 선택과 평가 분리</a> · <a href="https://docs.pytorch.org/docs/stable/generated/torch.nn.modules.rnn.LSTM.html">PyTorch LSTM</a> · <a href="https://xgboost.readthedocs.io/en/latest/parameter.html">XGBoost 파라미터</a></p></main></body></html>'''
    if plan['smoke']:
        (store/'preview.html').write_text(page,encoding='utf-8');return
    OUT.mkdir(exist_ok=True);(OUT/'전체모델_정책비교.html').write_text(page,encoding='utf-8')
    mirrored=True
    try:
        local=page.replace('href="보고서_모음.html"','href="index.html"').replace('href="핸드오버_정책_심화연구.html"','href="policy_research.html"')
        (ROOT/'viz_build/all_model_policy.html').write_text(local,encoding='utf-8')
    except PermissionError:mirrored=False
    (store/'report_sync.json').write_text(json.dumps(dict(time=when,original_report_mirrored=mirrored),ensure_ascii=False,indent=2),encoding='utf-8')
    summary=dict(round=display_round,complete=bool(result_rows),current_round=rid if not done else None,current_round_complete=done,completed_pairs=len(all_rows),total_pairs=total,
                 family_results={f:metrics([r for r in result_rows if r['family']==f]) for f in ORDER if any(r['family']==f for r in result_rows)} if result_rows else {})
    (store/'latest_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')

if __name__=='__main__':
    import sys
    generate(Path(sys.argv[1]) if len(sys.argv)>1 else OUT/'all_model_policy')
