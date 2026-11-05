"""Build a portable, self-contained Korean research report from completed results."""
import argparse, html, json
from collections import Counter
from pathlib import Path
import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
LABEL={'Network':'실제 망','A3':'고정 A3 · 3 dB','Rule':'검증으로 고른 규칙','Q':'표 Q-learning','DQN':'DQN','DQN+GRU':'DQN + GRU 예측','CQL':'CQL · 합성 고정 버퍼','PPO':'PPO'}
ORDER=list(LABEL)
def read(p):return json.loads(p.read_text(encoding='utf-8'))
def esc(s):return html.escape(str(s))
def fmt(x,n=2):return f'{x:,.{n}f}'
def table(headers,rows):
    return '<div class="table-wrap"><table><thead><tr>'+''.join(f'<th>{esc(h)}</th>' for h in headers)+'</tr></thead><tbody>'+''.join('<tr>'+''.join(f'<td>{v}</td>' for v in r)+'</tr>' for r in rows)+'</tbody></table></div>'

def main():
    p=argparse.ArgumentParser();p.add_argument('--run-name',default='policy_v2');p.add_argument('--page-name',default='policy_research.html');args=p.parse_args()
    out=ROOT/'analysis_output'/args.run_name
    m=read(out/'manifest.json');s=read(out/'summary.json');search=read(out/'search.json');choices=read(out/'choices.json');records=read(out/'records.json')
    progress=read(out/'progress.json');assert progress['status']=='complete'
    pol=s['policies']; n=len(m['data']); seeds=m['args']['seeds']; folds=m['args']['folds']
    feasible=[k for k in ORDER if k not in ('Network','A3') and pol[k]['delta']['weak_delta_pp']<=0 and pol[k]['delta']['rsrp_loss']<=1]
    best=max(feasible,key=lambda k:.5*pol[k]['delta']['ho_reduction']+.5*pol[k]['delta']['pp_reduction']) if feasible else None
    if best:
        d=pol[best]['delta']
        lead=f'평가 평균에서 신호 보호 조건을 만족한 정책 중 {LABEL[best]}의 HO·핑퐁 균형 점수가 가장 높았습니다.'
        detail=f'핸드오버 {d["ho_reduction"]:.1%}, 핑퐁 {d["pp_reduction"]:.1%} 감소. 평균 RSRP 손실 {d["rsrp_loss"]:.2f} dB, 약전계 비율 변화 {d["weak_delta_pp"]:+.3f}%p입니다.'
    else:
        lead='학습 정책의 감소율만으로 성공을 선언할 수 없습니다.'
        detail='평가 평균에서 약전계 증가 없음과 평균 RSRP 손실 1 dB 이내를 동시에 만족한 탐색 정책이 없습니다. 규칙과 고정 A3를 함께 비교해야 합니다.'
    rows=[];mdrows=[]
    for k in ORDER:
        v=pol[k];r=v['metrics'];d=v['delta'];ci=v['ci95'];ok=d['weak_delta_pp']<=1e-12 and d['rsrp_loss']<=1
        def interval(key,scale=1):return f'<small>95% CI {ci[key][0]*scale:+.2f} ~ {ci[key][1]*scale:+.2f}</small>'
        rows.append([f'<b>{LABEL[k]}</b>',f'{r["ho"]:,.1f}<small>시드 평균 건수</small>',f'{d["ho_reduction"]:.1%}'+interval('ho_reduction',100),f'{d["pp_reduction"]:.1%}'+interval('pp_reduction',100),f'{r["weak_share"]:.2%}<small>변화 {d["weak_delta_pp"]:+.3f}%p</small>',f'{d["rsrp_loss"]:+.2f} dB'+interval('rsrp_loss'),f'{r["forced"]:,.1f}',f'<span class="badge {"ok" if ok else "bad"}">{"충족" if ok else "미충족"}</span>'])
        mdrows.append(f'| {LABEL[k]} | {d["ho_reduction"]:.1%} | {d["pp_reduction"]:.1%} | {d["weak_delta_pp"]:+.3f} | {d["rsrp_loss"]:+.2f} | {r["forced"]:.1f} |')
    result_table=table(['정책','HO','HO 감소','핑퐁 감소','약전계 ≤−105 dBm','평균 RSRP 손실','강제 이동','신호 보호¹'],rows)
    fold_table=table(['분할','학습 세션','검증 세션','평가 세션'],[[f'{x["fold"]+1}',str(len(x['train'])),str(len(x['validation'])),str(len(x['test']))] for x in m['splits']])
    choice_table=table(['분할','정책','선택한 설정'],[[str(x['fold']+1),LABEL[x['family']],f'<code>{esc(json.dumps(x["config"],ensure_ascii=False))}</code>'] for x in choices])
    seed_table=table(['정책','시드','HO 감소','핑퐁 감소','약전계 변화 (%p)','RSRP 손실 (dB)'],[[LABEL[k],str(seed),f'{v["ho_reduction"]:.1%}',f'{v["pp_reduction"]:.1%}',f'{v["weak_delta_pp"]:+.3f}',f'{v["rsrp_loss"]:+.2f}'] for k in ORDER if k not in ('Network','A3','Rule') for seed,v in zip(seeds,pol[k]['seed_results'])])
    acts=[]
    for act in sorted({r['activity'] for r in records}):
        from policy_research import aggregate,delta
        subset=[r for r in records if r['activity']==act]
        def agg(name):
            ss=sorted({r['session'] for r in subset if r['policy']==name}); rr=[]
            for sid in ss:
                vals=[r['metrics'] for r in subset if r['policy']==name and r['session']==sid]
                rr.append({k:float(np.mean([r[k] for r in vals])) for k in vals[0]})
            return aggregate(rr)
        base=agg('Network')
        for k in ORDER:
            d=delta(agg(k),base)
            acts.append([{'car':'차량','subway':'지하철','walking':'도보','home':'실내'}.get(act,act),LABEL[k],f'{d["ho_reduction"]:.1%}',f'{d["pp_reduction"]:.1%}',f'{d["weak_delta_pp"]:+.3f}',f'{d["rsrp_loss"]:+.2f}'])
    act_table=table(['이동수단','정책','HO 감소','핑퐁 감소','약전계 변화 (%p)','RSRP 손실 (dB)'],acts)
    data_table=table(['세션','이동수단','행 수','SHA-256 (앞 12자)'],[[esc(x['file']),esc(x['activity']),f'{x["rows"]:,}',f'<code>{x["sha256"][:12]}</code>'] for x in m['data']])
    payload=json.dumps(dict(summary=s,search=search,choices=choices,manifest=m,records=records),ensure_ascii=False).replace('</','<\\/')
    sections='''
    <section id="results"><div class="overline">01 / OUT-OF-FOLD EVALUATION</div><h2>감소율과 신호 품질을 함께 봅니다</h2><p>각 세션은 바깥 평가 분할에 정확히 한 번 들어갑니다. 학습 모델은 시드별 평가값을 먼저 계산한 뒤 평균했습니다. 작은 소수 건수는 시드 평균이므로 생깁니다.</p>RESULT_TABLE<p class="caption">¹ 신호 보호 = 약전계 행 비율 증가 없음 + 실제 망 대비 평균 RSRP 손실 ≤1 dB. 이것은 점추정치 기준이며, 신뢰구간 전체에 대한 보장은 아닙니다. 논문 목표인 HO ≥30%·핑퐁 ≥80% 감소는 별도 확인이 필요합니다.</p><div id="target-check"></div>
    <h3>평가 결과의 절충 관계</h3><p>오른쪽일수록 핸드오버를 더 줄이고, 아래쪽일수록 신호 손실이 적습니다. 점을 선택하면 해당 정책의 수치와 신뢰구간을 볼 수 있습니다.</p><div class="plot" id="result-plot"></div><div id="point-info" class="note" aria-live="polite">점 또는 정책 버튼을 선택하세요.</div><div class="legend" id="policy-buttons"></div></section>
    <section id="search"><div class="overline">02 / VALIDATION SEARCH</div><h2>최고점 하나보다 탐색 지형을 남깁니다</h2><p>아래 점은 모두 검증 세션 결과입니다. 평가 결과와 섞어 그리지 않았습니다. 분할마다 훈련된 정책과 기준 망이 달라 점을 직접 합쳐 평균 순위를 내면 안 됩니다.</p><div class="controls"><label>분할 <select id="fold"></select></label><label>정책 <select id="family"><option value="all">전체</option></select></label></div><div id="search-plot" class="plot"></div><p class="caption">테두리가 진한 점은 현재 표시 집합에서 HO 감소·RSRP 손실·약전계 변화의 세 축에 대해 비지배인 점입니다. 이 절충 경계는 검증 데이터의 탐색 결과입니다.</p><div id="search-info" class="note" aria-live="polite">점에 마우스를 올리거나 선택하면 보상과 설정이 표시됩니다.</div><details><summary>분할별 최종 선택 설정</summary>CHOICE_TABLE</details></section>
    <section id="design"><div class="overline">03 / EXPERIMENT DESIGN</div><h2>설정을 고르는 데이터와 성능을 재는 데이터를 분리했습니다</h2><div class="flow"><div><b>학습 세션</b><span>모델·GRU 학습<br>합성 버퍼 생성</span></div><i>→</i><div><b>검증 세션</b><span>보상·규칙·CQL 계수 선택<br>외부 평가에 접근하지 않음</span></div><i>→</i><div><b>평가 세션</b><span>선택 후 최종 3시드 평가<br>설정 재선택에 사용하지 않음</span></div></div>FOLD_TABLE
    <h3>선택 기준을 모델마다 바꾸지 않았습니다</h3><p>검증 기준 망 대비 약전계 비율 증가가 없고, 평균 RSRP 손실이 1 dB 이하인 정책을 먼저 고릅니다. 그 안에서 0.5 × HO 감소율 + 0.5 × 핑퐁 감소율을 최대화합니다. 조건 충족 후보가 없으면 약전계 증가(%p)와 1 dB 초과 손실(dB)의 합이 최소인 후보를 택합니다. 서로 단위가 다른 위반량을 합산하는 이 규칙 자체도 연구자의 설계 선택입니다.</p>
    <h3>탐색 범위와 학습 예산</h3><ul><li>규칙 27개/분할: 히스테리시스 {3, 6, 9} dB × 복귀 추가 문턱 {0, 3, 6} dB × A2 {없음, −95, −90} dBm.</li><li>Q-learning 보상 27개/분할: HO 비용 1 고정, 핑퐁 {1, 3, 9}, 약전계 {2, 5, 15}, 신호 차이 {0.03, 0.1, 0.3}. 탐색 15 epoch, 최종 60 epoch.</li><li>신경망은 Q 검증 상위 3개 보상을 공유합니다. DQN·DQN+GRU·PPO는 각 3개, CQL은 보수 계수 {0.1, 1}까지 6개/분할입니다. 탐색 13 epoch, 최종 40 epoch. 전체 후보 수는 동일하지 않습니다.</li><li>최종 정책 시드 {0, 1, 2}. GRU 예측기는 분할마다 시드 1개·15 epoch로 고정하므로 GRU 자체의 시드 불확실성은 별도로 추정하지 않았습니다.</li><li>TTT=1행, 상태 구간, 네트워크 크기와 학습률은 고정했습니다. 이번 결과는 이 범위의 탐색이며 전역 최적화가 아닙니다.</li></ul>
    <h3>재현 정보</h3><div id="repro"></div><details><summary>어떤 세션이 어디에 들어갔는지 보기</summary><div id="split-details"></div></details></section>
    <section id="methods"><div class="overline">04 / MODEL & ENVIRONMENT</div><h2>정책 학습에서 실제로 계산한 것</h2><div class="two"><article><h3>공통 재생 환경</h3><p>관측된 서빙·이웃 신호만 시간순으로 재생합니다. 같은 주파수의 이웃이 최소 3 dB 강한 신선한 행에서 보류/이동 중 하나를 고릅니다. 셀이 목록에서 사라지면 실제 서빙 셀로 강제 이동합니다.</p><p>보상 = −HO −w<sub>pp</sub>·핑퐁 −w<sub>weak</sub>·약전계 −w<sub>gap</sub>·min(최강 셀 차이, 15 dB). 행마다 신호 비용을 누적하며, 강제 이동도 HO·핑퐁 비용에 포함합니다.</p></article><article><h3>할인과 상태</h3><p>결정 시점 사이의 행별 보상을 γ=0.99로 누적하고, 다음 상태 값에는 γ<sup>경과 행 수</sup>를 곱합니다. 세션 종료 시 다음 상태 값은 0입니다. 기존 Q 코드의 추가 할인 한 번을 없앴고, γ도 기존 0.9에서 변경했으므로 이전 결과와 차이가 모델만의 효과는 아닙니다.</p><p>표 Q 상태는 RSRP 4구간 × 이웃 차이 3구간 × 복귀 2 × 체류 3 = 72개입니다.</p></article></div>
    <h3>모델별 구현</h3><ul><li><b>DQN:</b> 은닉 64·64 tanh MLP, Double-DQN 목표, 경험 재생 최대 50,000개, 2 epoch마다 목표망 동기화. Adam 3×10⁻⁴, Huber 손실, gradient clip 5.</li><li><b>PPO:</b> 같은 크기의 정책·가치망, clip 0.2, GAE λ=0.95, entropy 0.01, 가치 손실 계수 0.5, epoch당 4회 업데이트. 재생 환경 안에서 새 행동을 샘플링합니다.</li><li><b>CQL:</b> DQN 손실에 α·[logsumexp Q(s,·) − Q(s,a)]를 더합니다. 학습 세션에 세 가지 규칙과 15% 무작위 행동을 적용해 만든 버퍼를 고정하고, 이후 새 경험을 수집하지 않습니다. 실제 망의 오프라인 RL 검증과 구분해야 합니다.</li><li><b>DQN + GRU:</b> 직전 12행의 관측 서빙 RSRP와 동결 마스크로 10초 뒤 관측 서빙 신호 변화량을 예측합니다. GRU 은닉 16, Adam 0.002, L1 손실. 입력은 과거 방향 채움만 하며 예측 대상 행은 10~12.5초 뒤·신선한 관측만 씁니다.</li></ul>
    <p class="note">신경망 상태는 서빙 RSRP, 이웃 차이, 복귀 여부, 체류 시간, 최근 30초 HO 수, 이동수단 one-hot, 직전 판단 행 대비 신호 변화, GRU 예측값의 10개 슬롯입니다. GRU 비교 이외 모델은 마지막 슬롯을 0으로 둡니다. GRU는 <b>실제 관측 서빙 신호</b>를 예측하므로 가상 정책이 유지한 셀의 미래를 예측하는 모델로 해석하면 안 됩니다.</p></section>
    <section id="uncertainty"><div class="overline">05 / UNCERTAINTY</div><h2>시드와 세션 편차를 숨기지 않습니다</h2><p>95% 신뢰구간은 세션별 시드 평균을 구한 뒤, 같은 세션의 정책과 기준 망을 묶어 2,000회 재표집한 백분위 구간입니다. 학습을 매번 다시 돌린 구간이 아니며, 분할·학습 모델에 조건부입니다. 여러 모델을 비교하는 데 따른 다중 비교 보정은 하지 않았습니다.</p><details open><summary>최종 학습 시드별 결과</summary>SEED_TABLE</details><details><summary>이동수단별 평가 결과</summary>ACT_TABLE</details></section>
    <section id="limits"><div class="overline">06 / PAPER NOTES</div><h2>논문에 쓸 수 있는 주장과 남은 검증</h2><div class="two"><article class="positive"><h3>이번 결과가 뒷받침하는 범위</h3><ul><li>기록 재생 환경에서, 세션 분리와 내부 검증을 적용한 정책 비교 결과.</li><li>정해진 보상·규칙 탐색 범위에서 HO·핑퐁 감소와 신호 품질의 절충 관계.</li><li>최종 학습 시드별 변동과 세션 묶음 재표집으로 본 불확실성.</li></ul></article><article class="caution"><h3>추가 근거가 필요한 주장</h3><ul><li>실제 망에서도 같은 감소율·처리량 향상이 나온다는 주장.</li><li>약전계 행 비율을 실제 연결 끊김(RLF)이나 시간 비율로 대체하는 주장.</li><li>CQL을 실망 행동 로그로 학습한 오프라인 RL 성과라고 부르는 주장.</li><li>한 모델이 일반적으로 우수하다는 주장.</li></ul></article></div><ol><li><b>기존에 살펴본 데이터입니다.</b> 이번 분할에서 평가값으로 설정을 고르지는 않았지만, 같은 31개 세션을 과거 실험에서 이미 사용했습니다. 새 날짜·새 경로의 외부 평가가 필요합니다.</li><li><b>관측과 셀 식별 한계:</b> (EARFCN, PCI)는 넓은 지역에서 재사용될 수 있습니다. 가상 서빙 셀의 누락은 강제 이동을 만들고, 정책별 강제 이동 수가 달라집니다. 실제 망의 반응·부하·간섭 변화는 없습니다.</li><li><b>동결·샘플링:</b> cell_info_age_ms &gt;8초 행은 판단/신호 집계에서 제외하지만 셀 소실에 따른 강제 이동은 발생할 수 있습니다. 약전계는 신선한 행 수 기준이고, 이벤트 행과 불규칙 간격 때문에 시간 비율과 다릅니다.</li><li><b>독립성:</b> 같은 사용자·단말·반복 경로의 세션은 서로 독립적이지 않을 수 있습니다. 날짜/경로 그룹 평가, 시간순 평가와 더 많은 단말 수집이 후속 과제입니다.</li><li><b>다음 실험:</b> 새 외부 평가셋 고정 → TTT·상태 경계 민감도 → 보상·신경망 예산 균등화 → 시간 가중 약전계와 강제 이동 민감도 → 실제 측정/망 검증 순서가 적절합니다.</li><li><b>Jev:</b> API 결제·호출을 하지 않았고 기존 보류 상태입니다. SINR 누락 문제는 이번 정책 실험에서 새로 검증하지 않았습니다.</li></ol></section>
    <section id="reproduce"><div class="overline">07 / REPRODUCIBILITY</div><h2>코드와 근거를 바로 찾을 수 있게</h2><p>저장소 루트 <code>C:\\Users\\admin\\NetworkTrackerApp</code></p><ul><li><code>analysis/policy_research.py</code> — 데이터 해시, 분할, 보상·규칙 탐색, Q/DQN/CQL/PPO/GRU, 최종 평가·신뢰구간.</li><li><code>analysis/policy_research_report.py</code> — 현재 HTML·Markdown 리포트 생성.</li><li><code>analysis/test_policy_research.py</code> — 기존 A3와 집계 일치, 결정 사이 할인·종료 보상, 분할 누출, 추론 일치, 상태 72개 확인.</li><li><code>analysis_output/policy_v2/</code> — manifest, search, choices, records, summary, curves, CSV와 분할·시드별 가중치.</li><li><code>analysis/model_compare.py</code> — 10/28의 지도학습 A·B와 기존 Q 비교. 이번 실험과 별도 보존.</li></ul><pre>python -m unittest discover -s analysis -p test_policy_research.py -v
python analysis/policy_research.py --run-name policy_v2_repeat --folds 5 --seeds 0 1 2 --search-epochs 15 --epochs 60 --neural-epochs 40 --bootstrap 2000
python analysis/policy_research_report.py --run-name policy_v2_repeat</pre><p class="caption">실행 폴더와 무관하게 저장소 위치를 기준으로 데이터를 읽습니다. 동일 이름 결과 폴더는 덮어쓰지 않습니다. 재실행은 새 run-name을 지정합니다. HTML 안에 결과·탐색·분할 데이터를 포함해 오프라인에서도 표와 그래프가 작동합니다.</p><div class="controls"><button onclick="download('summary.json',DATA.summary)">평가 요약 JSON</button><button onclick="download('manifest.json',DATA.manifest)">분할·데이터 해시 JSON</button><button onclick="download('search.json',DATA.search)">탐색 전체 JSON</button><button onclick="download('records.json',DATA.records)">세션별 원자료 JSON</button></div><details><summary>전체 데이터 목록</summary>DATA_TABLE</details><h3>방법 참고 문헌</h3><ul><li><a href="https://www.nature.com/articles/nature14236">Mnih et al., Human-level control through deep reinforcement learning, Nature (2015)</a></li><li><a href="https://arxiv.org/abs/1707.06347">Schulman et al., Proximal Policy Optimization Algorithms (2017)</a></li><li><a href="https://arxiv.org/abs/2006.04779">Kumar et al., Conservative Q-Learning for Offline Reinforcement Learning (2020)</a></li></ul></section>
    '''
    for key,value in [('RESULT_TABLE',result_table),('CHOICE_TABLE',choice_table),('FOLD_TABLE',fold_table),('SEED_TABLE',seed_table),('ACT_TABLE',act_table),('DATA_TABLE',data_table)]:sections=sections.replace(key,value)
    css='''
    :root{--ink:#172b43;--muted:#607187;--blue:#215fd1;--line:#dbe3ec;--bg:#f4f6fa;--green:#12705b;--red:#b2433b}*{box-sizing:border-box}html{scroll-behavior:smooth;scroll-padding-top:24px}body{margin:0;color:var(--ink);background:var(--bg);font:15px/1.8 "Malgun Gothic",system-ui,sans-serif}a{color:var(--blue)}.shell{max-width:1500px;margin:auto;display:grid;grid-template-columns:230px minmax(0,1fr);gap:48px;padding:40px 44px 100px}aside{position:sticky;top:32px;align-self:start}.brand{font:600 12px/1.6 Consolas,monospace;letter-spacing:.12em}.brand i{display:inline-block;width:12px;height:12px;background:var(--blue);border-radius:3px;margin-right:10px}nav{display:grid;gap:6px;margin-top:42px}nav a{text-decoration:none;color:var(--muted);padding:9px 12px;border-radius:6px;font-size:13px}nav a:hover{background:#e5ebf6;color:var(--blue)}.navnum{font:12px Consolas;margin-right:10px;color:#9aa6b6}.aside-foot{margin-top:40px;color:var(--muted);font-size:12px;border-top:1px solid var(--line);padding-top:18px}.overline{font:11px/1.5 Consolas,monospace;letter-spacing:.13em;color:var(--blue);margin-bottom:16px}h1{font-size:clamp(32px,3.6vw,52px);line-height:1.3;letter-spacing:-.055em;margin:18px 0 22px}h2{font-size:27px;line-height:1.45;letter-spacing:-.04em;margin:0 0 20px}h3{font-size:18px;line-height:1.5;margin:30px 0 10px}p{margin:12px 0;color:var(--muted)}.hero{border-top:4px solid var(--blue);padding:28px 0 32px}.hero p{max-width:740px;font-size:17px}.tag{display:inline-block;padding:4px 9px;background:#e8efff;color:var(--blue);font-size:12px;border-radius:4px;margin-right:6px}.metrics{display:grid;grid-template-columns:repeat(4,1fr);border:1px solid var(--line);border-radius:10px;overflow:hidden;background:white;margin:28px 0}.metric{padding:22px;border-right:1px solid var(--line)}.metric:last-child{border:0}.metric strong{font:600 29px/1.2 Consolas,monospace;display:block;margin-top:10px}.metric span{font-size:12px;color:var(--muted)}.finding{background:#eaf0fb;border:1px solid #d3dff5;border-left:4px solid var(--blue);padding:22px 26px;margin:20px 0}.finding b{font-size:19px}.finding p{margin:8px 0 0;font-size:14px}section{background:white;border:1px solid var(--line);border-radius:12px;padding:32px;margin:24px 0;overflow:hidden}.table-wrap{overflow-x:auto;margin:18px -2px}table{border-collapse:collapse;width:100%;font-size:12px;font-variant-numeric:tabular-nums}th{text-align:left;background:#f0f4f9;color:#52647b;font-weight:600;white-space:nowrap}th,td{border-bottom:1px solid var(--line);padding:12px 11px;vertical-align:top}td small{display:block;font-size:10px;white-space:nowrap;color:var(--muted);margin-top:4px}.badge{padding:3px 6px;white-space:nowrap;border-radius:4px;font-size:11px}.ok{background:#e5f3ed;color:var(--green)}.bad{background:#fff0e9;color:var(--red)}.caption{font-size:12px}.note{font-size:13px;background:#f3f6fb;padding:16px;border-radius:7px;color:var(--muted);min-height:56px}.two{display:grid;grid-template-columns:1fr 1fr;gap:20px}.two article{background:#f7f9fc;border:1px solid var(--line);border-radius:8px;padding:20px}.two h3{margin:0 0 10px}.two p,.two ul{font-size:13px}.two .positive{border-top:3px solid var(--green)}.two .caution{border-top:3px solid #b77933}ul,ol{padding-left:22px;color:var(--muted)}li{margin:8px 0}.flow{display:flex;align-items:center;gap:16px;margin:24px 0}.flow div{flex:1;background:#edf2fc;border:1px solid #d7e1f4;border-radius:8px;padding:18px}.flow b{display:block}.flow span{font-size:12px;color:var(--muted);display:block;margin-top:8px}.flow i{color:var(--blue);font-size:22px}details{border-top:1px solid var(--line);padding:16px 0;margin-top:20px}summary{cursor:pointer;font-size:14px;font-weight:600}.controls{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin:18px 0}select,button{font:inherit;font-size:12px;border:1px solid #cdd9eb;border-radius:6px;background:white;color:var(--ink);padding:8px 11px}button{cursor:pointer}button:hover{background:#edf2fc}label{color:var(--muted);font-size:12px}.plot{background:#fbfcfe;border:1px solid #e4eaf2;border-radius:9px;margin:20px 0}.plot svg{width:100%;height:auto;display:block}.plot circle{cursor:pointer}.plot text{font-family:Consolas,"Malgun Gothic",sans-serif}.legend{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}.legend button{border-left:4px solid var(--c)}code{font:12px Consolas,monospace;background:#eff3f9;border-radius:3px;padding:2px 5px;overflow-wrap:anywhere}pre{background:#172b43;color:#dae6fa;padding:20px;border-radius:8px;font:12px/1.9 Consolas,monospace;overflow:auto}footer{font-size:12px;color:var(--muted);padding:18px 0}.file-list{max-height:350px;overflow:auto} @media(max-width:1100px){.shell{grid-template-columns:170px minmax(0,1fr);gap:24px;padding:26px 20px}.metric{padding:16px}section{padding:24px}}@media(max-width:760px){.shell{display:block;padding:20px 14px}aside{position:static}.brand{margin-bottom:15px}nav{display:flex;overflow:auto;margin:0 0 20px;gap:2px}nav a{white-space:nowrap}.aside-foot{display:none}.hero{padding-top:20px}.metrics{grid-template-columns:1fr 1fr}.metric:nth-child(-n+2){border-bottom:1px solid var(--line)}section{padding:20px 16px}.two{grid-template-columns:1fr}.flow{display:grid;gap:8px}.flow i{display:none}h2{font-size:23px}}@media print{body{background:white;font-size:10pt}.shell{display:block;padding:0}aside,.controls,button{display:none}section{break-inside:auto;border:0;padding:12px 0}.hero{padding:10px 0}h1{font-size:28pt}h2{break-after:avoid}.plot{break-inside:avoid}.metrics{margin:12px 0}details{display:block}details>*{display:block}a{color:inherit}pre{white-space:pre-wrap;color:black;background:#eee}}
    '''
    js='''
    const colors={Network:'#8896a8',A3:'#526c94',Rule:'#228978',Q:'#4376d7',DQN:'#b36acd','DQN+GRU':'#e89038',CQL:'#c85163',PPO:'#534fb2'};
    const names=LABELS, order=Object.keys(names);const S=DATA.summary.policies;
    const nf=(v,n=2)=>Number(v).toFixed(n);function el(id){return document.getElementById(id)}
    function download(name,data){let a=document.createElement('a'),u=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}));a.href=u;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(u),1000)}
    function svgNode(tag,attrs,text){let n=document.createElementNS('http://www.w3.org/2000/svg',tag);Object.entries(attrs).forEach(([k,v])=>n.setAttribute(k,v));if(text!==undefined)n.textContent=text;return n}
    function scatter(id,points,callback,frontier=false){
      let root=el(id);root.replaceChildren();const W=840,H=400,L=78,R=28,T=26,B=66;
      let svg=svgNode('svg',{viewBox:`0 0 ${W} ${H}`,role:'img','aria-label':'핸드오버 감소율 대 평균 RSRP 손실 절충 그래프'});root.append(svg);
      if(!points.length)return;
      const xs=points.map(p=>p.x),ys=points.map(p=>p.y);let xmin=Math.min(0,...xs)-3,xmax=Math.max(5,...xs)+3,ymin=Math.min(0,...ys)-.15,ymax=Math.max(.5,...ys)+.2;
      const x=v=>L+(v-xmin)/(xmax-xmin)*(W-L-R),y=v=>H-B-(v-ymin)/(ymax-ymin)*(H-T-B);
      for(let i=0;i<=5;i++){let vx=xmin+(xmax-xmin)*i/5,vy=ymin+(ymax-ymin)*i/5;
        svg.append(svgNode('line',{x1:x(vx),x2:x(vx),y1:T,y2:H-B,stroke:'#e0e7f0'}));svg.append(svgNode('text',{x:x(vx),y:H-B+25,'text-anchor':'middle',fill:'#718197','font-size':12},nf(vx,0)+'%'));
        svg.append(svgNode('line',{x1:L,x2:W-R,y1:y(vy),y2:y(vy),stroke:'#e0e7f0'}));svg.append(svgNode('text',{x:L-12,y:y(vy)+4,'text-anchor':'end',fill:'#718197','font-size':12},nf(vy,1)));}
      if(1>ymin&&1<ymax){svg.append(svgNode('line',{x1:L,x2:W-R,y1:y(1),y2:y(1),stroke:'#b77933','stroke-dasharray':'5 5'}));svg.append(svgNode('text',{x:W-R-4,y:y(1)-7,'text-anchor':'end',fill:'#a16b29','font-size':11},'1 dB 보호 기준'));}
      svg.append(svgNode('text',{x:(L+W-R)/2,y:H-15,'text-anchor':'middle',fill:'#52647b','font-size':13},'핸드오버 감소율 →'));
      svg.append(svgNode('text',{x:17,y:H/2,transform:`rotate(-90 17 ${H/2})`,'text-anchor':'middle',fill:'#52647b','font-size':13},'평균 RSRP 손실 (dB)'));
      points.forEach(p=>{const nondom=frontier&&!points.some(q=>q!==p&&q.x>=p.x&&q.y<=p.y&&q.w<=p.w&&(q.x>p.x||q.y<p.y||q.w<p.w));
        let c=svgNode('circle',{cx:x(p.x),cy:y(p.y),r:frontier?5:8,fill:colors[p.k],opacity:frontier?.72:1,stroke:nondom?'#172b43':'white','stroke-width':nondom?2:1.5,tabindex:0,role:'button','aria-label':`${names[p.k]} HO ${nf(p.x)}%, RSRP ${nf(p.y)} dB`});
        c.append(svgNode('title',{},`${names[p.k]} · HO ${nf(p.x)}% · RSRP ${nf(p.y)} dB`));c.addEventListener('mouseenter',()=>callback(p));c.addEventListener('click',()=>callback(p));c.addEventListener('focus',()=>callback(p));c.addEventListener('keydown',e=>{if(e.key==='Enter')callback(p)});svg.append(c);
      });
    }
    function showPolicy(k){let v=S[k],d=v.delta,ci=v.ci95;el('point-info').textContent=`${names[k]} | HO 감소 ${nf(d.ho_reduction*100,1)}% [${nf(ci.ho_reduction[0]*100,1)}, ${nf(ci.ho_reduction[1]*100,1)}] | 핑퐁 감소 ${nf(d.pp_reduction*100,1)}% | 약전계 변화 ${nf(d.weak_delta_pp,3)}%p | RSRP 손실 ${nf(d.rsrp_loss)} dB | 강제 이동 ${nf(d.forced,1)}건`}
    scatter('result-plot',order.map(k=>({k,x:S[k].delta.ho_reduction*100,y:S[k].delta.rsrp_loss,w:S[k].delta.weak_delta_pp})),p=>showPolicy(p.k));
    order.forEach(k=>{let b=document.createElement('button');b.textContent=names[k];b.style.setProperty('--c',colors[k]);b.onclick=()=>showPolicy(k);el('policy-buttons').append(b)});
    const counts=DATA.manifest.splits.length;for(let i=0;i<counts;i++)el('fold').add(new Option(String(i+1),i));
    [...new Set(DATA.search.map(r=>r.family))].forEach(k=>el('family').add(new Option(names[k],k)));
    function update(){let pts=DATA.search.filter(r=>r.fold===Number(el('fold').value)&&(el('family').value==='all'||r.family===el('family').value)).map(r=>({k:r.family,x:r.ho_reduction*100,y:r.rsrp_loss,w:r.weak_delta_pp,r}));scatter('search-plot',pts,p=>{el('search-info').textContent=`분할 ${p.r.fold+1} · ${names[p.k]} | ${JSON.stringify(p.r.config)} | HO 감소 ${nf(p.x)}% · 핑퐁 감소 ${nf(p.r.pp_reduction*100)}% · 약전계 변화 ${nf(p.w,3)}%p · RSRP 손실 ${nf(p.y)} dB`},true)}
    el('fold').onchange=update;el('family').onchange=update;update();
    el('repro').textContent=`Python ${DATA.manifest.python} · PyTorch ${DATA.manifest.torch} · NumPy ${DATA.manifest.numpy} · pandas ${DATA.manifest.pandas} · CPU, torch threads=2 · 본 실행 ${nf(DATA.summary.elapsed_seconds/60,1)}분 · 코드 SHA-256 ${DATA.manifest.code_sha256}`;
    DATA.manifest.splits.forEach(s=>{let h=document.createElement('h3');h.textContent=`분할 ${s.fold+1}`;el('split-details').append(h);for(let k of ['train','validation','test']){let p=document.createElement('p');p.style.fontSize='12px';p.textContent=k+': '+s[k].join(', ');el('split-details').append(p)}});
    const passed=order.filter(k=>!['Network','A3'].includes(k)&&S[k].delta.ho_reduction>=.3&&S[k].delta.pp_reduction>=.8&&S[k].delta.weak_delta_pp<=0&&S[k].delta.rsrp_loss<=1);
    el('target-check').className='note';el('target-check').textContent=passed.length?'4개 논문 목표를 평가 평균에서 모두 충족한 정책: '+passed.map(k=>names[k]).join(', ')+' (신뢰구간 전체 충족은 별도 확인).':'4개 논문 목표(HO ≥30%, 핑퐁 ≥80%, 약전계 증가 없음, 평균 RSRP 손실 ≤1 dB)를 평가 평균에서 모두 충족한 탐색 정책은 없습니다.';
    '''.replace('LABELS',json.dumps(LABEL,ensure_ascii=False))
    nav=''.join(f'<a href="#{id}"><span class="navnum">{i:02}</span>{label}</a>' for i,(id,label) in enumerate([('results','최종 평가'),('search','보상·규칙 탐색'),('design','데이터 분리'),('methods','모델과 환경'),('uncertainty','불확실성'),('limits','논문 메모'),('reproduce','코드·재현')],1))
    page=f'''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>핸드오버 정책 심화 연구 · NetworkTrackerApp</title><style>{css}</style></head><body><div class="shell"><aside><div class="brand"><i></i>NETWORK TRACKER<br>RESEARCH NOTE / 02</div><nav>{nav}</nav><div class="aside-foot">2026.11.01<br>재생 기반 정책 연구<br><a href="index.html">← 결과 모음</a><br><a href="model_research.html">10/28 1차 모델 연구</a><br><button onclick="window.print()">인쇄 / PDF로 저장</button></div></aside><main><header class="hero"><div class="overline">MEASURED NETWORK DATA / POLICY RESEARCH</div><span class="tag">완료된 실행 결과</span><span class="tag">내부 검증 + 바깥 평가</span><h1>덜 옮기고,<br>신호는 지킬 수 있을까.</h1><p>보상 가중치와 규칙을 탐색하고, 표 Q-learning에서 신경망 정책까지 같은 실측 재생 환경에서 비교했습니다. 논문에 필요한 설정·불확실성·한계를 결과와 함께 남깁니다.</p><div class="metrics"><div class="metric"><span>수집 세션</span><strong>{n}</strong><span>2026.08.04 — 10.25</span></div><div class="metric"><span>평가 분할 × 최종 시드</span><strong>{folds} × {len(seeds)}</strong><span>세션 간 중복 분리</span></div><div class="metric"><span>검증 후보 평가</span><strong>{len(search)}</strong><span>규칙·보상·보수 계수</span></div><div class="metric"><span>학습 정책 계열</span><strong>5</strong><span>Q / DQN / +GRU / CQL / PPO</span></div></div><div class="finding"><b>{lead}</b><p>{detail}</p><p>이 수치는 기록 재생 환경의 내부 평가입니다. 실제 망 개선이나 새로운 외부 데이터의 일반화 성능을 입증하지는 않습니다.</p></div></header>{sections}<footer>NetworkTrackerApp · Research Note 02 · 모든 표와 그래프는 같은 완료 실행에서 생성했습니다. 학습 시간 자체는 성능·연구 완성도의 지표가 아닙니다.</footer></main></div><script>const DATA={payload};{js}</script></body></html>'''
    if m['args'].get('smoke'):
        page=page.replace('완료된 실행 결과','시험 실행 · 본 실험 결과 아님').replace('<h1>','<p class="note">레이아웃 점검용 소규모 실행입니다. 아래 학습 예산 설명은 본 실험 기준이며 이 시험 실행의 예산과 다릅니다.</p><h1>',1)
    target=ROOT/'viz_build'/args.page_name;target.write_text(page,encoding='utf-8')
    md=f'''# 핸드오버 정책 심화 연구 — 2026-11-01

{lead}

{detail}

## 평가 결과

| 정책 | HO 감소 | 핑퐁 감소 | 약전계 변화 (%p) | 평균 RSRP 손실 (dB) | 강제 이동 |
|---|---:|---:|---:|---:|---:|
'''+ '\n'.join(mdrows)+f'''

## 설계와 재현

- 세션 {n}개, 바깥 {folds}겹 평가, 학습 내 검증 세션 분리. 최종 정책 시드 {seeds}.
- 전체 검증 후보 {len(search)}개. 규칙 27개, Q 보상 27개, 신경망 보상 후보는 Q 상위 3개 공유. CQL 계수 0.1/1.
- 약전계 행 비율 증가 없음 및 평균 신호 손실 ≤1 dB 우선, 이후 HO/핑퐁 감소율 동일 가중 합으로 설정 선택.
- 평가 세션별 시드 평균 후 쌍을 맞춘 세션 부트스트랩 2,000회. 학습 분할에 조건부인 구간.
- 실행 시간 {s['elapsed_seconds']/60:.1f}분, CPU PyTorch {m['torch']}.
- 코드: `analysis/policy_research.py`, 리포트: `analysis/policy_research_report.py`.
- 근거 데이터와 분할: `analysis_output/{args.run_name}/manifest.json`. 세션별 평가: `session_metrics.csv`.
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
'''
    (out/'report.md').write_text(md,encoding='utf-8')
    (out/'report.html').write_text(page,encoding='utf-8')
    print(target)

if __name__=='__main__':main()
