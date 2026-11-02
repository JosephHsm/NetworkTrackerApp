"""Bounded validation-only continuation. Keeps policy_v2 outer evaluation frozen.

python analysis/policy_continue.py
No paid APIs, no external transmission. One batch per invocation, exclusive lock.
"""
from __future__ import annotations
import argparse, hashlib, html, itertools, json, math, time
from datetime import datetime, timezone, timedelta
from pathlib import Path
import numpy as np
import torch
import policy_research as pr

ROOT=pr.ROOT
EXPORT=Path(r'C:\Users\admin\Documents\Codex\2026-11-01\new-chat\outputs')
STORE=EXPORT/'policy_continuous'
FAMILIES=('DQN','PPO','CQL','DQN+GRU')
COLORS={'Q':'#4376d7','DQN':'#b36acd','PPO':'#534fb2','CQL':'#c85163','DQN+GRU':'#e89038'}
SEEDS=(11,29)
CONFIRM_SEED_BASES=(47,83,101,137,173)
def read(p):return json.loads(p.read_text(encoding='utf-8'))
def stamp():return datetime.now(timezone(timedelta(hours=9))).isoformat(timespec='seconds')
def key(c):return tuple(round(float(v),6) for v in c)

def initial_state():
    old=read(ROOT/'analysis_output/policy_v2/search.json')
    manifest=read(ROOT/'analysis_output/policy_v2/manifest.json')
    best={}
    for fold in range(5):
        for family in ('Q',)+FAMILIES:
            vals=[r for r in old if r['fold']==fold and r['family']==family]
            def rank(r):
                violation=max(r['weak_delta_pp'],0)+max(r['rsrp_loss']-1,0)
                return (violation<=1e-12,-violation,.5*(r['ho_reduction']+r['pp_reduction']))
            r=max(vals,key=rank)
            best[f'{fold}:{family}']=dict(config=r['config'],source='policy_v2 validation',last_batch=0)
    return dict(version=1,completed_batches=0,no_improvement_batches=0,best=best,tried={},history=[],data=manifest['data'],created=stamp())

def propose(anchor,n,rng,seen,batch):
    candidates=[list(anchor)];used={key(anchor)}|{key(c) for c in seen}
    bounds=np.array([[.25,81],[.5,45],[.005,1.5]])
    attempts=0
    while len(candidates)<n:
        attempts+=1
        if attempts>1000:raise RuntimeError('Candidate sampling exhausted')
        if rng.random()<.25:
            p=np.exp(rng.uniform(np.log(bounds[:,0]),np.log(bounds[:,1])))
        else:
            width=.55 if batch%8==0 else max(.2,.55/(1+batch/16))
            p=np.array(anchor)*np.exp(rng.normal(0,width,3))
        p=np.round(np.clip(p,bounds[:,0],bounds[:,1]),6)
        if key(p) not in used:candidates.append(p.tolist());used.add(key(p))
    return candidates

def material_improvement(new,old):
    # Both values come from identical seeds/budgets on the same validation split.
    if new[0]!=old[0]:return new[0]>old[0]
    if abs(new[1]-old[1])>1e-12:return new[1]>old[1]+.002
    return new[2]>old[2]+.002

def rank_delta(d):
    violation=max(d['weak_delta_pp'],0)+max(d['rsrp_loss']-1,0)
    return (violation<=1e-12,-violation,.5*(d['ho_reduction']+d['pp_reduction']))

def assess(train,val,base,family,cfg,seeds,smoke=False):
    rows=[];models=[];seed_results=[]
    for seed in seeds:
        if family=='Q':agent=pr.Table(seed).train(train,cfg['reward'],1 if smoke else 30)
        else:
            agent=pr.Neural('DQN' if family.startswith('DQN') else family,seed,gru=family.endswith('GRU'))
            agent.train(train,cfg['reward'],1 if smoke else 20,cfg.get('alpha',0.))
        rr=pr.evaluate(val,agent,cfg['reward']);rows.extend(rr);models.append(agent)
        seed_results.append(dict(seed=seed,delta=pr.delta(pr.aggregate(rr),base),sessions=[dict(session=s.name,metrics=r) for s,r in zip(val,rr)]))
    score=pr.aggregate([{k:sum(r[k] for r in rows)/len(seeds) for k in pr.FIELDS}])
    d=pr.delta(score,base)
    sd={k:float(np.std([r['delta'][k] for r in seed_results],ddof=1)) if len(seeds)>1 else 0. for k in d}
    return pr.selection(score,base),dict(config=cfg,seeds=list(seeds),delta=d,seed_sd=sd,seed_results=seed_results),models

def confirmation_decision(anchor,candidate):
    a,c=anchor[1]['seed_results'],candidate[1]['seed_results']
    if [r['seed'] for r in a]!=[r['seed'] for r in c]:raise ValueError('Paired confirmation requires identical seed order')
    wins=sum(rank_delta(y['delta'])>rank_delta(x['delta']) for x,y in zip(a,c))
    mean_improved=material_improvement(candidate[0],anchor[0])
    accepted=mean_improved and wins>=len(a)//2+1
    return dict(accepted=accepted,mean_improved=mean_improved,seed_wins=wins,seed_count=len(a))

def continuation_section(state,trials):
    e=html.escape; rows=[]
    for name,b in sorted(state['best'].items()):
        if 'validation' not in b:continue
        fold,family=name.split(':');d=b['validation']
        rows.append(f'<tr><td>{int(fold)+1}</td><td>{e(family)}</td><td>{b.get("validation_seed_count",2)}</td><td><code>{e(str(b["config"]))}</code></td><td>{d["ho_reduction"]:.1%}</td><td>{d["pp_reduction"]:.1%}</td><td>{d["weak_delta_pp"]:+.3f}</td><td>{d["rsrp_loss"]:+.2f}</td></tr>')
    points=[];W,H,L,R,T,B=840,390,72,24,26,58
    if trials:
        xs=[r['delta']['ho_reduction']*100 for r in trials];ys=[r['delta']['rsrp_loss'] for r in trials]
        xmin,xmax=min(0,min(xs))-3,max(5,max(xs))+3;ymin,ymax=min(0,min(ys))-.2,max(.5,max(ys))+.2
        xx=lambda v:L+(v-xmin)/(xmax-xmin)*(W-L-R)
        yy=lambda v:H-B-(v-ymin)/(ymax-ymin)*(H-T-B)
        for i in range(6):
            x=xmin+(xmax-xmin)*i/5;y=ymin+(ymax-ymin)*i/5
            points.append(f'<path d="M{xx(x):.1f},{T}V{H-B} M{L},{yy(y):.1f}H{W-R}" stroke="#e0e7f0"/><text x="{xx(x):.1f}" y="{H-B+23}" text-anchor="middle" font-size="12" fill="#607187">{x:.0f}%</text><text x="{L-10}" y="{yy(y)+4:.1f}" text-anchor="end" font-size="12" fill="#607187">{y:.1f}</text>')
        for r in trials:
            d=r['delta'];title=f'배치 {r["batch"]} / 분할 {r["fold"]+1} / {r["family"]} / {r["config"]} / HO {d["ho_reduction"]:.1%}, PP {d["pp_reduction"]:.1%}, weak {d["weak_delta_pp"]:+.3f}%p, loss {d["rsrp_loss"]:.2f}dB'
            points.append(f'<circle cx="{xx(d["ho_reduction"]*100):.1f}" cy="{yy(d["rsrp_loss"]):.1f}" r="4.5" opacity=".65" fill="{COLORS[r["family"]]}" tabindex="0"><title>{e(title)}</title></circle>')
        points.append(f'<text x="{W/2}" y="{H-12}" text-anchor="middle" font-size="13" fill="#607187">핸드오버 감소율 → (검증 세션)</text><text transform="rotate(-90 17 {H/2})" x="17" y="{H/2}" text-anchor="middle" font-size="13" fill="#607187">평균 RSRP 손실 (dB)</text>')
    history=''.join(f'<tr><td>{r["batch"]}</td><td>{e(r["time"])}</td><td>{e(r["neural_family"])}</td><td>{r["trials"]}</td><td>{r["updates"]}</td><td>{r["seconds"]/60:.1f}분</td></tr>' for r in state['history'])
    return f'''<section id="continuous"><div class="overline">LIVE / VALIDATION-ONLY OPTIMIZATION</div><h2>가중치 지속 탐색 · 배치 {state['completed_batches']}</h2><p>마지막 완료: {e(state.get('updated',state['created']))}. 누적 후보 평가 {len(trials):,}개. 시드 11·29로 후보를 선별한 뒤, 기존 설정과 최강 신규 후보를 별도의 새 시드 5개로 확인합니다. 확인 시드는 배치마다 새로 정하며, 비교하는 두 설정에는 같은 시드를 씁니다. Q-learning은 매번, 신경망은 DQN → PPO → CQL → DQN+GRU 순환입니다.</p><div class="note"><b>아래 결과는 추가 검증 탐색입니다.</b> 1절의 최종 평가·신뢰구간은 11/01 첫 실험 그대로 동결했습니다. 평가 세션을 재평가하거나 그 점수로 가중치를 고르지 않습니다. 같은 검증셋을 반복 사용하므로 이 섹션의 향상을 일반화 성능 향상이라고 주장할 수 없습니다. 새 날짜·새 경로에서 독립 평가가 필요합니다.</div><p>탐색 범위: 핑퐁 비용 0.25~81, 약전계 비용 0.5~45, 신호 차이 비용 0.005~1.5. 기존 후보 주변 로그 정규 탐색 75% + 전체 범위 로그 균등 탐색 25%. HO 비용 1, γ=0.99와 기존 분할 고정. 배치당 기본 후보 4개/분할/정책(현재값 포함), Q 30 epoch·신경망 20 epoch. 선별은 2시드, 기존 설정과 신규 후보의 확인은 별도 5시드입니다. 확인 평균의 선택 기준 개선이 0.002를 넘고 5개 시드 중 3개 이상에서 후보의 선택 기준 점수가 더 나을 때만 가중치를 교체합니다. 시드별 결과와 표준편차를 기록하며 통계적 유의성을 입증한 것으로 해석하지 않습니다.</p><div class="plot"><svg viewBox="0 0 {W} {H}" role="img" aria-label="지속 탐색 검증 후보의 절충 그래프">{''.join(points)}</svg></div><p class="caption">Q 파랑 · DQN 보라 · PPO 남보라 · CQL 빨강 · DQN+GRU 주황. 점의 툴팁은 배치·분할·설정·수치를 보여줍니다. 이 그래프는 2시드 선별 결과입니다. 5시드 확인 결과는 별도 표에 제시합니다. 서로 다른 분할의 점을 합쳐 최종 순위를 내지 않습니다.</p><details open><summary>분할별 현재 가중치와 마지막 비교의 검증 결과</summary><div class="table-wrap"><table><thead><tr><th>분할</th><th>정책</th><th>확인 시드 수</th><th>현재 설정</th><th>HO 감소</th><th>핑퐁 감소</th><th>약전계 변화 (%p)</th><th>RSRP 손실 (dB)</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div></details><details><summary>누적 실행 이력</summary><div class="table-wrap"><table><thead><tr><th>배치</th><th>완료 (한국 시간)</th><th>Q와 함께 탐색한 정책</th><th>후보 평가</th><th>가중치 교체</th><th>실행 시간</th></tr></thead><tbody>{history}</tbody></table></div></details><p class="caption">연속 무개선 배치 {state['no_improvement_batches']}개. 12개 배치 동안 기준 이상 개선이 없으면 계산을 쉬고 새 실험 설계가 필요함을 기록합니다. 이는 전역 최적점의 증명이 아닙니다. 새 CSV는 자동으로 학습에 섞지 않고 외부 평가 후보로 보류합니다.</p></section>'''

def render(state,store=STORE):
    trials=[]
    for p in sorted(store.glob('batch_*/trials.json')):
        if (p.parent/'complete.json').exists():trials.extend(read(p))
    section=continuation_section(state,trials)
    comparisons=[]
    for p in sorted(store.glob('batch_*/comparisons.json')):
        if (p.parent/'complete.json').exists():comparisons.extend(read(p))
    if comparisons:
        rows=[]
        for r in comparisons:
            a,c=r['anchor_delta'],r['candidate_delta']
            rows.append(f'<tr><td>{r["batch"]}</td><td>{r["fold"]+1}</td><td>{html.escape(r["family"])}</td><td>{r["seed_wins"]}/{r["seed_count"]}</td><td>{a["ho_reduction"]:.1%} → {c["ho_reduction"]:.1%}</td><td>{a["pp_reduction"]:.1%} → {c["pp_reduction"]:.1%}</td><td>{a["weak_delta_pp"]:+.3f} → {c["weak_delta_pp"]:+.3f}</td><td>{a["rsrp_loss"]:+.2f} → {c["rsrp_loss"]:+.2f}</td><td>{"교체" if r["accepted"] else "기존 유지"}</td></tr>')
        confirmation_html='<details open><summary>새 5시드 확인 · 기존 설정 → 신규 후보</summary><p class="caption">후보를 동결한 뒤 선별과 별도인 5개 시드로 비교했습니다. 승수는 신호 보호를 우선한 선택 기준의 시드별 비교이며, 유의성 검정 결과가 아닙니다. 아래 수치도 평가 세션이 아닌 검증 세션 값입니다.</p><div class="table-wrap"><table><thead><tr><th>배치</th><th>분할</th><th>정책</th><th>후보 승수</th><th>HO 감소</th><th>핑퐁 감소</th><th>약전계 변화 (%p)</th><th>RSRP 손실 (dB)</th><th>결정</th></tr></thead><tbody>'+''.join(rows)+'</tbody></table></div></details>'
        section=section.replace('</section>',confirmation_html+'</section>')
    base=(ROOT/'analysis_output/policy_v2/report.html').read_text(encoding='utf-8')
    page=base.replace('<section id="results">',section+'<section id="results">',1)
    page=page.replace('<nav>','<nav><a href="research_progress.html"><span class="navnum">↗</span>쉽게 읽는 연구 진행</a><a href="#continuous"><span class="navnum">↻</span>가중치 지속 탐색</a>',1)
    page=page.replace('완료된 실행 결과','1차 평가 보존 · 추가 검증 탐색',1)
    target=ROOT/'viz_build/policy_research.html'
    try:
        target.write_text(page,encoding='utf-8');mirrored=True
    except PermissionError:mirrored=False
    EXPORT.mkdir(parents=True,exist_ok=True)
    export=page.replace('href="index.html"','href="file:///C:/Users/admin/NetworkTrackerApp/viz_build/index.html"').replace('href="model_research.html"','href="file:///C:/Users/admin/NetworkTrackerApp/viz_build/model_research.html"')
    (EXPORT/'핸드오버_정책_심화연구.html').write_text(export,encoding='utf-8')
    (store/'latest_report.html').write_text(page,encoding='utf-8')
    md='# 가중치 지속 탐색\n\n최종 평가는 policy_v2에 동결. 이 파일은 검증 탐색 이력입니다.\n\n'+''.join(f'- 배치 {r["batch"]}: {r["time"]}, Q + {r["neural_family"]}, 후보 {r["trials"]}개, 가중치 교체 {r["updates"]}개, {r["seconds"]/60:.1f}분.\n' for r in state['history'])
    (EXPORT/'지속탐색_이력.md').write_text(md,encoding='utf-8')
    return mirrored

def run(args):
    store=ROOT/'analysis_output'/args.store
    store.mkdir(parents=True,exist_ok=True)
    lock=store/'batch.lock'
    try: handle=lock.open('x',encoding='utf-8')
    except FileExistsError:raise SystemExit('A batch lock exists. Inspect the current run before starting another.')
    try:
        handle.write(stamp());handle.close()
        state=read(store/'state.json') if (store/'state.json').exists() else initial_state()
        # Freeze the exact original corpus; never absorb fresh evaluation candidates.
        for item in state['data']:
            file=ROOT/'trackingcsv/new'/item['file']
            if hashlib.sha256(file.read_bytes()).hexdigest()!=item['sha256']:raise RuntimeError(f'Original corpus changed: {file.name}')
        known={x['file'] for x in state['data']}
        state['reserved_new_sessions']=[p.name for p in (ROOT/'trackingcsv/new').glob('*.csv') if p.name not in known]
        if state['no_improvement_batches']>=12:
            state['status']='plateau';pr.dump(store/'state.json',state);print('PLATEAU: no further compute without revised validation/data design.');return
        prior_ids=[int(p.name.split('_')[1]) for p in store.glob('batch_*') if p.is_dir() and p.name.split('_')[1].isdigit()]
        batch=max([state.get('last_batch',0)]+prior_ids)+1;folder=store/f'batch_{batch:04d}';folder.mkdir(exist_ok=False)
        family=FAMILIES[state['completed_batches']%len(FAMILIES)];rng=np.random.default_rng(314159+batch)
        manifest=read(ROOT/'analysis_output/policy_v2/manifest.json')
        seeds=(11,) if args.smoke else SEEDS
        confirm_seeds=tuple(batch*10000+s for s in CONFIRM_SEED_BASES[:2 if args.smoke else 5])
        trials=[];confirmations=[];comparisons=[];updates=0;start=time.time()
        pr.dump(folder/'plan.json',dict(batch=batch,time=stamp(),neural_family=family,seeds=seeds,confirmation_seeds=confirm_seeds,candidates=args.candidates,smoke=args.smoke,
                confirmation_rule='Freeze strongest novel candidate after screening; compare candidate and incumbent on new paired seeds; promote only on material mean improvement and strict majority seed wins',
                code_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),original_manifest_sha256=hashlib.sha256((ROOT/'analysis_output/policy_v2/manifest.json').read_bytes()).hexdigest(),evaluation='validation only; outer test never evaluated'))
        for split in manifest['splits'][:1 if args.smoke else 5]:
            fi=split['fold'];train=[pr.Session(ROOT/'trackingcsv/new'/f) for f in split['train']]
            val=[pr.Session(ROOT/'trackingcsv/new'/f) for f in split['validation']]
            base=pr.aggregate([s.base for s in val])
            if family=='DQN+GRU':pr.fit_forecast(train,train+val,fi,epochs=15)
            for fam in ('Q',family):
                tag=f'{fi}:{fam}';previous=state['best'][tag];anchor=previous['config']['reward']
                candidates=propose(anchor,args.candidates,rng,state['tried'].get(tag,[]),batch)
                results=[]
                for ci,reward in enumerate(candidates):
                    cfg=dict(reward=reward,alpha=previous['config'].get('alpha',0.))
                    rank,trial,models=assess(train,val,base,fam,cfg,seeds,args.smoke)
                    trial.update(batch=batch,fold=fi,family=fam,anchor=ci==0,stage='screening')
                    trials.append(trial);results.append((rank,trial,models))
                    pr.dump(folder/'trials.json',trials)
                # Freeze ONE strongest novel candidate before observing confirmation seeds.
                novel=max(results[1:],key=lambda x:x[0]);frozen_cfg=novel[1]['config']
                pr.dump(folder/f'fold{fi}_{fam.replace("+","_")}_frozen_candidate.json',dict(config=frozen_cfg,anchor=results[0][1]['config'],confirmation_seeds=confirm_seeds))
                confirmed=[]
                for is_anchor,cfg in ((True,results[0][1]['config']),(False,frozen_cfg)):
                    rank,trial,models=assess(train,val,base,fam,cfg,confirm_seeds,args.smoke)
                    trial.update(batch=batch,fold=fi,family=fam,anchor=is_anchor,stage='confirmation')
                    confirmations.append(trial);confirmed.append((rank,trial,models))
                    pr.dump(folder/'confirmations.json',confirmations)
                decision=confirmation_decision(confirmed[0],confirmed[1]);changed=decision['accepted']
                comparisons.append(dict(batch=batch,fold=fi,family=fam,**decision,
                    screening_mean_improved=material_improvement(novel[0],results[0][0]),
                    anchor_config=confirmed[0][1]['config'],candidate_config=confirmed[1][1]['config'],
                    anchor_delta=confirmed[0][1]['delta'],candidate_delta=confirmed[1][1]['delta']))
                pr.dump(folder/'comparisons.json',comparisons)
                chosen=confirmed[1] if changed else confirmed[0]
                updates+=int(changed)
                state['best'][tag]=dict(config=chosen[1]['config'],validation=chosen[1]['delta'],seed_sd=chosen[1]['seed_sd'],validation_seeds=list(confirm_seeds),validation_seed_count=len(confirm_seeds),last_batch=batch,changed=changed,source='validation confirmation')
                state['tried'].setdefault(tag,[]).extend(candidates[1:])
                for seed,agent in zip(confirm_seeds,chosen[2]):
                    stem=folder/f'fold{fi}_{fam.replace("+","_")}_seed{seed}'
                    if fam=='Q':np.save(str(stem)+'.npy',agent.q)
                    else:torch.save(agent.model.state_dict(),str(stem)+'.pt')
                pr.dump(folder/'progress.json',dict(fold=fi,family=fam,trials=len(trials),seconds=time.time()-start))
                print(f'batch {batch}, fold {fi+1}, {fam}: screened {len(candidates)} x {len(seeds)} seeds; confirmed incumbent vs candidate on {len(confirm_seeds)} new seeds, candidate wins={decision["seed_wins"]}/{len(confirm_seeds)}; changed={changed}',flush=True)
        state['completed_batches']+=1;state['last_batch']=batch;state['no_improvement_batches']=0 if updates else state['no_improvement_batches']+1
        state['updated']=stamp();state['status']='active'
        state['history'].append(dict(batch=batch,time=stamp(),neural_family=family,trials=len(trials),confirmation_trials=len(confirmations),confirmation_seeds=len(confirm_seeds),fit_count=len(trials)*len(seeds)+len(confirmations)*len(confirm_seeds),updates=updates,seconds=time.time()-start))
        pr.dump(folder/'complete.json',dict(time=stamp(),updates=updates,seconds=time.time()-start))
        pr.dump(store/'state.json',state)
        if not args.smoke:state['report_mirrored']=render(state,store);pr.dump(store/'state.json',state)
        print(f'COMPLETE batch {batch}: {len(trials)} validation configurations, {updates} weight updates; {time.time()-start:.1f}s',flush=True)
    finally:
        lock.unlink(missing_ok=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--store',default=str(STORE));parser.add_argument('--candidates',type=int,default=4);parser.add_argument('--smoke',action='store_true')
    args=parser.parse_args()
    if args.candidates<2:parser.error('At least anchor + one novel candidate required')
    if args.smoke and args.store in ('policy_continuous',str(STORE)):parser.error('Use a distinct --store for smoke runs')
    torch.set_num_threads(2);torch.use_deterministic_algorithms(True);run(args)
