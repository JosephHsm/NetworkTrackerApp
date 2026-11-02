"""Audit a completed inclusive study from saved evidence, without retraining."""
import argparse,hashlib,json,math
from pathlib import Path
import policy_research as pr
import model_policy_sweep as sweep

def read(p):return json.loads(p.read_text(encoding='utf-8'))
def rank(d):
    v=max(d['weak_delta_pp'],0)+max(d['rsrp_loss']-1,0)
    return (v<=1e-12,-v,.5*(d['ho_reduction']+d['pp_reduction']))
def improved(new,old):
    if new[0]!=old[0]:return new[0]>old[0]
    if abs(new[1]-old[1])>1e-12:return new[1]>old[1]+.002
    return new[2]>old[2]+.002

def verify(store,round_id=None):
    store=Path(store);state=read(store/'state.json');rid=round_id or state['completed_rounds']
    folder=store/f'round_{rid:04d}';plan=read(folder/'plan.json');complete=read(folder/'complete.json')
    assert not plan['smoke'],'Smoke output is not a completed production study'
    for file,key in [('model_policy_sweep.py','engine_sha256'),('policy_research.py','policy_module_sha256'),('policy_continue.py','selection_module_sha256')]:
        assert hashlib.sha256((folder/file).read_bytes()).hexdigest()==plan[key]
    folds={r['fold']:r for r in plan['manifest']['splits']}
    rows=[]
    for path in folder.glob('fold*.json'):
        r=read(path)
        if isinstance(r,dict) and 'selected' in r and 'screening' in r:rows.append(r)
    assert len(rows)==45 and len({(r['fold'],r['family']) for r in rows})==45
    assert len(plan['confirmation_seeds'])==5 and not set(plan['confirmation_seeds'])&set(plan['screening_seeds'])
    fits=0
    for r in rows:
        sp=folds[r['fold']];assert len(r['screening'])==4 and len(r['confirmation'])==2
        for stage,trials in [('screening',r['screening']),('confirmation',r['confirmation'])]:
            expected=plan[stage+'_seeds']
            for trial in trials:
                assert trial['seeds']==expected
                assert [v['seed'] for v in trial['seed_results']]==expected
                for seed in trial['seed_results']:
                    names={v['session'] for v in seed['sessions']}
                    assert names==set(sp['validation']) and not names&set(sp['test'])
                    forecast=seed.get('forecast')
                    if forecast:
                        assert forecast['train']==sp['train'] and forecast['validation']==sp['validation']
                        assert forecast['seed']==seed['seed'] and math.isfinite(forecast['validation_mae'])
                    assert all(math.isfinite(v) for v in seed['delta'].values())
        frozen=read(folder/f'fold{r["fold"]}_{r["family"].replace("+","_")}_frozen.json')
        a,b=r['confirmation'];assert a['role']=='anchor' and b['role']=='candidate'
        assert a['config']==frozen['anchor_config'] and b['config']==frozen['candidate_config']
        wins=sum(rank(y['delta'])>rank(x['delta']) for x,y in zip(a['seed_results'],b['seed_results']))
        mean_improved=improved(rank(b['delta']),rank(a['delta']))
        accepted=mean_improved and wins>=3
        assert r['decision']==dict(accepted=accepted,mean_improved=mean_improved,seed_wins=wins,seed_count=5)
        assert r['selected']==(b if accepted else a)
        if r['family']!='Rule':fits+=4*2+2*5
    if rid==1:
        for fold in folds:
            controls=[next(c for c in r['confirmation'] if c['role']=='anchor')['config'] for r in rows if r['fold']==fold and r['family'] in ('DQN','DQN+XGBoost','DQN+RNN','DQN+LSTM','DQN+GRU')]
            assert len(controls)==5 and all(c==controls[0] for c in controls)
    assert fits==720==complete['policy_fit_count']
    report=dict(round=rid,complete_pairs=45,screening_configurations=180,confirmation_configurations=90,
        policy_fit_count=fits,validation_sessions_only=True,forecast_training_sessions_only=True,
        paired_confirmation_seeds=True,initial_dqn_anchor_controls_equal=rid==1,source_snapshots_verified=True,
        updates=sum(r['decision']['accepted'] for r in rows))
    pr.dump(folder/'verification.json',report);print(json.dumps(report))
    return report

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--store',default=str(sweep.STORE));p.add_argument('--round',type=int)
    args=p.parse_args();verify(args.store,args.round)
