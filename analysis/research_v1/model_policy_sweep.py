"""Equal-candidate model-policy search. Frozen policy_v2 outer tests are unused."""
from __future__ import annotations
import argparse,copy,hashlib,json,time
from pathlib import Path
import numpy as np
import pandas as pd
import torch
from torch import nn
import policy_research as pr
import policy_continue as pc

ROOT=pr.ROOT;OUT=pc.EXPORT;STORE=OUT/'all_model_policy';W=12
FAMILIES=('Q','DQN','PPO','CQL','DQN+XGBoost','DQN+RNN','DQN+LSTM','DQN+GRU','Rule')
PREDICTORS={'DQN+XGBoost':'xgboost','DQN+RNN':'rnn','DQN+LSTM':'lstm','DQN+GRU':'gru'}
SCREEN_SEEDS=(11,29)

def default_config(family):
    return dict(reward=[3.,5.,.1],policy_lr=.0003,q_alpha=.1,cql_alpha=.1,
        forecast=dict(hidden=16,lr=.002,depth=3,trees=80,tree_lr=.05),rule=dict(hyst=3.,pen=6.,a2=None))

def candidates(family,anchor,round_id,n=4):
    rng=np.random.default_rng(20261102+round_id*1000+FAMILIES.index(family)*13)
    result=[copy.deepcopy(anchor)]
    for i in range(1,n):
        c=copy.deepcopy(anchor)
        if round_id==1:c['reward']=[[3.,5.,.1],[1.,15.,.03],[9.,5.,.3],[3.,15.,.1]][i%4]
        else:
            lo=np.array([.25,.5,.005]);hi=np.array([81.,45.,1.5])
            vals=np.exp(rng.uniform(np.log(lo),np.log(hi))) if rng.random()<.25 else np.array(anchor['reward'])*np.exp(rng.normal(0,.4,3))
            c['reward']=np.round(np.clip(vals,lo,hi),6).tolist()
        c['policy_lr']=[.0003,.001,.0001,.0005][i%4]
        c['q_alpha']=[.1,.03,.3,.05][i%4];c['cql_alpha']=[.1,1.,.03,.3][i%4]
        c['forecast'].update(hidden=[16,32,16,32][i%4],lr=[.002,.001,.003,.002][i%4],depth=[3,2,4,3][i%4],trees=[80,120,80,160][i%4],tree_lr=[.05,.1,.03,.05][i%4])
        c['rule']=[dict(hyst=3.,pen=6.,a2=None),dict(hyst=6.,pen=3.,a2=None),dict(hyst=3.,pen=6.,a2=-95.),dict(hyst=6.,pen=6.,a2=-90.)][i%4]
        result.append(c)
    return result

def forecast_data(sessions):
    windows={};targets={}
    for s in sessions:
        d=s.df;rs=pd.to_numeric(d.rsrp_dbm,errors='coerce').ffill().fillna(-105).to_numpy(float)
        t=d.timestamp.to_numpy()/1000.;age=d.cell_info_age_ms.to_numpy(float)
        x=np.column_stack(((rs+90)/20,(~(age<=8000)).astype(float)))
        padded=np.pad(x,((W-1,0),(0,0)),mode='edge')
        windows[s.name]=np.stack([padded[j:j+W] for j in range(len(rs))]).astype('float32')
        k=np.searchsorted(t,t+10);ok=k<len(t);kc=np.minimum(k,len(t)-1)
        ok&=(t[kc]-t<=12.5)&(age<=8000)&(age[kc]<=8000)
        targets[s.name]=((rs[kc]-rs).astype('float32'),ok)
    return windows,targets

class Forecast(nn.Module):
    def __init__(self,kind,hidden):
        super().__init__();self.cell={'rnn':nn.RNN,'lstm':nn.LSTM,'gru':nn.GRU}[kind](2,hidden,batch_first=True);self.fc=nn.Linear(hidden,1)
    def forward(self,x):return self.fc(self.cell(x)[0][:,-1]).squeeze(1)

def fit_forecast(kind,cfg,seed,train,val,prepared,cache,epochs):
    windows,targets=prepared
    provenance=dict(kind=kind,config=cfg,seed=seed,epochs=epochs,train=[s.name for s in train],validation=[s.name for s in val])
    key=hashlib.sha256(json.dumps(provenance,sort_keys=True).encode()).hexdigest()[:24]
    directory=cache/key;record=directory/'complete.json';start=time.time()
    if record.exists():
        m=pc.read(record)
        with np.load(directory/'predictions.npz') as arrays:
            for s in train+val:s.pred=arrays[s.name].copy()
        return dict(m,cache_hit=True,load_seconds=time.time()-start)
    directory.mkdir(parents=True,exist_ok=True)
    X=np.concatenate([windows[s.name][targets[s.name][1]] for s in train])
    Y=np.concatenate([targets[s.name][0][targets[s.name][1]]/10 for s in train])
    assert np.isfinite(X).all() and np.isfinite(Y).all();torch.manual_seed(seed)
    if kind=='xgboost':
        import xgboost as xgb
        model=xgb.XGBRegressor(n_estimators=cfg['trees'],max_depth=cfg['depth'],learning_rate=cfg['tree_lr'],objective='reg:squarederror',subsample=.8,colsample_bytree=.8,min_child_weight=10,n_jobs=2,random_state=seed,verbosity=0)
        model.fit(X.reshape(len(X),-1),Y);model.save_model(directory/'forecast.ubj')
        predict=lambda a:model.predict(a.reshape(len(a),-1))*10
    else:
        model=Forecast(kind,cfg['hidden']);opt=torch.optim.Adam(model.parameters(),lr=cfg['lr'])
        for _ in range(epochs):
            for idx in torch.randperm(len(X)).split(256):
                loss=nn.functional.l1_loss(model(torch.from_numpy(X[idx])),torch.from_numpy(Y[idx]))
                opt.zero_grad();loss.backward();nn.utils.clip_grad_norm_(model.parameters(),5);opt.step()
        model.eval();torch.save(model.state_dict(),directory/'forecast.pt')
        def predict(a):
            with torch.no_grad():return np.concatenate([model(torch.from_numpy(b)).numpy()*10 for b in np.array_split(a,max(1,len(a)//512))])
    saved={};absolute=[]
    for s in train+val:s.pred=predict(windows[s.name]).astype('float32');saved[s.name]=s.pred
    for s in val:
        y,ok=targets[s.name];absolute.extend(np.abs(s.pred[ok]-y[ok]).tolist())
    meta=dict(provenance,fit_seconds=time.time()-start,training_rows=len(X),validation_rows=len(absolute),validation_mae=float(np.mean(absolute)),cache_key=key,cache_hit=False)
    np.savez(directory/'predictions.npz',**saved);pr.dump(record,meta);return meta

class TunedTable(pr.Table):
    def train(self,sessions,reward,epochs,alpha=.1):
        for ep in range(epochs):
            self.eps=max(.02,.3*(1-ep/max(epochs*.7,1)))
            for i in self.rng.permutation(len(sessions)):
                _,tr=pr.replay(sessions[i],self,reward,collect=True)
                for x,a,r,n,d,done in tr:
                    k=pr.discrete(x);target=r+(0 if done else d*self.q[pr.discrete(n)].max());self.q[k,a]+=alpha*(target-self.q[k,a])
        self.eps=0.;return self

def assess(family,cfg,seeds,train,val,prepared,cache,policy_epochs,forecast_epochs):
    base=pr.aggregate([s.base for s in val]);all_rows=[];seed_results=[];models=[]
    for seed in seeds:
        begin=time.time();forecast=None
        for s in train+val:s.pred=np.zeros(len(s.fr),dtype='float32')
        if family in PREDICTORS:forecast=fit_forecast(PREDICTORS[family],cfg['forecast'],seed,train,val,prepared,cache,forecast_epochs)
        counter=dict(episodes=0,rows=0);original=pr.replay
        def measured(*args,**kwargs):
            counter['episodes']+=1;counter['rows']+=len(args[0].fr);return original(*args,**kwargs)
        trained=time.time();agent=None
        try:
            pr.replay=measured
            if family=='Rule':
                r=cfg['rule'];policy=lambda x:(r['a2'] is None or x[0]*20-90<r['a2']) and x[1]*15>=r['hyst']+r['pen']*x[2]
            elif family=='Q':agent=TunedTable(seed).train(train,cfg['reward'],policy_epochs,cfg['q_alpha']);policy=agent
            else:
                kind=family if family in ('PPO','CQL') else 'DQN';agent=pr.Neural(kind,seed,gru=family in PREDICTORS)
                for group in agent.opt.param_groups:group['lr']=cfg['policy_lr']
                agent.train(train,cfg['reward'],policy_epochs,cfg['cql_alpha'] if kind=='CQL' else 0.);policy=agent
        finally:pr.replay=original
        train_seconds=time.time()-trained;evaluated=time.time();hyst=cfg['rule']['hyst'] if family=='Rule' else 3.
        rr=pr.evaluate(val,policy,cfg['reward'],hyst=hyst);all_rows.extend(rr);models.append(agent)
        seed_results.append(dict(seed=seed,delta=pr.delta(pr.aggregate(rr),base),sessions=[dict(session=s.name,metrics=r) for s,r in zip(val,rr)],forecast=forecast,policy_train_seconds=train_seconds,validation_seconds=time.time()-evaluated,policy_training_replay=counter,total_seconds=time.time()-begin))
    score=pr.aggregate([{k:sum(r[k] for r in all_rows)/len(seeds) for k in pr.FIELDS}]);d=pr.delta(score,base)
    sd={k:float(np.std([r['delta'][k] for r in seed_results],ddof=1)) if len(seeds)>1 else 0. for k in d}
    return pr.selection(score,base),dict(config=cfg,seeds=list(seeds),delta=d,seed_sd=sd,seed_results=seed_results),models

def save_models(directory,family,seeds,agents):
    directory.mkdir(parents=True,exist_ok=True)
    for seed,agent in zip(seeds,agents):
        if agent is None:continue
        target=directory/f'seed{seed}'
        if family=='Q':np.save(str(target)+'.npy',agent.q)
        else:torch.save(agent.model.state_dict(),str(target)+'.pt')

def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()

def run(args):
    store=Path(args.store);store.mkdir(parents=True,exist_ok=True)
    manifest=pc.read(ROOT/'analysis_output/policy_v2/manifest.json')
    lock=pc.STORE/'batch.lock'
    try:f=lock.open('x',encoding='utf-8')
    except FileExistsError:raise SystemExit('Another policy experiment owns batch.lock; do not start twice.')
    try:
        f.write(pc.stamp());f.close();process_start=time.time()
        state_path=store/'state.json'
        state=pc.read(state_path) if state_path.exists() else dict(version=1,engine_sha256=digest(Path(__file__)),
            created=pc.stamp(),completed_rounds=0,current_round=None,best={},history=[],status='active',no_improvement_rounds=0)
        if state['engine_sha256']!=digest(Path(__file__)):raise RuntimeError('Learning code changed: use a new study store, preserving existing evidence.')
        for item in manifest['data']:
            if digest(ROOT/'trackingcsv/new'/item['file'])!=item['sha256']:raise RuntimeError('Frozen source data changed: '+item['file'])
        known={r['file'] for r in manifest['data']}
        state['reserved_new_sessions']=[p.name for p in (ROOT/'trackingcsv/new').glob('*.csv') if p.name not in known]
        if state['no_improvement_rounds']>=12:
            state['status']='plateau';pr.dump(state_path,state);print('PLATEAU: revise data/design before more compute.');return
        rid=state['current_round'] or state['completed_rounds']+1;folder=store/f'round_{rid:04d}'
        folder.mkdir(exist_ok=True)
        families=('Q','DQN','DQN+XGBoost','DQN+RNN','DQN+LSTM','DQN+GRU','PPO','CQL','Rule') if not args.smoke else ('Q','DQN','DQN+XGBoost','DQN+RNN','DQN+LSTM','DQN+GRU','PPO','CQL','Rule')
        splits=manifest['splits'][:1] if args.smoke else manifest['splits']
        if (folder/'plan.json').exists():
            plan=pc.read(folder/'plan.json')
            if plan['engine_sha256']!=digest(Path(__file__)):raise RuntimeError('Cannot resume a round with changed learning code')
        else:
            configs={}
            for sp in splits:
                for fam in families:
                    tag=f'{sp["fold"]}:{fam}'
                    anchor=state['best'].get(tag,{}).get('config',default_config(fam))
                    configs[tag]=candidates(fam,anchor,rid,args.candidates)
            plan=dict(round=rid,time=pc.stamp(),engine_sha256=digest(Path(__file__)),policy_module_sha256=digest(Path(pr.__file__)),
                selection_module_sha256=digest(Path(pc.__file__)),manifest=manifest,families=list(families),configs=configs,
                screening_seeds=[11] if args.smoke else list(SCREEN_SEEDS),
                confirmation_seeds=[900000+rid*10000+s for s in (47,83) if args.smoke] if args.smoke else [900000+rid*10000+s for s in (47,83,101,137,173)],
                policy_epochs=1 if args.smoke else args.policy_epochs,forecast_epochs=1 if args.smoke else args.forecast_epochs,
                candidates_per_family=args.candidates,smoke=args.smoke,
                comparison='Equal candidate counts and paired seeds; same train/validation sessions, reward definition, constraints and replay. Algorithm/data-use and total forecast compute differ and are measured.',
                limitations=['Repeated validation, not new external test.','Forecast target is observed serving stream, not counterfactual virtual-cell future.',
                    'CQL uses synthetic replay behavior.','Finite coupled candidate search, not exhaustive or global optimization.','Fold validation sets can overlap; fold averages are not independent external tests.'])
            pr.dump(folder/'plan.json',plan)
            for name,module in [('model_policy_sweep.py',__file__),('policy_research.py',pr.__file__),('policy_continue.py',pc.__file__)]:
                (folder/name).write_bytes(Path(module).read_bytes())
        state['current_round']=rid;state['status']='running';pr.dump(state_path,state)
        render_report(store)
        print(f'Inclusive round {rid}: {len(families)} families x {len(splits)} folds; {args.candidates} candidates, paired confirmation seeds={plan["confirmation_seeds"]}',flush=True)
        updates=[];completed=[]
        for sp in splits:
            fi=sp['fold'];train=[pr.Session(ROOT/'trackingcsv/new'/name) for name in sp['train']]
            val=[pr.Session(ROOT/'trackingcsv/new'/name) for name in sp['validation']]
            prepared=forecast_data(train+val)
            for family in families:
                tag=f'{fi}:{family}';safe=f'fold{fi}_{family.replace("+","_")}'
                checkpoint=folder/(safe+'.json')
                if checkpoint.exists():
                    result=pc.read(checkpoint);completed.append(result);updates.append(result['decision']['accepted']);continue
                start=time.time();screen=[]
                for ci,cfg in enumerate(plan['configs'][tag]):
                    rank,trial,_=assess(family,cfg,plan['screening_seeds'],train,val,prepared,store/'forecast_cache',plan['policy_epochs'],plan['forecast_epochs'])
                    trial.update(candidate=ci,stage='screening');screen.append((rank,trial,[]))
                    pr.dump(folder/(safe+'_screening.json'),[r[1] for r in screen])
                novel=max(screen[1:],key=lambda r:r[0])
                frozen=dict(anchor_config=screen[0][1]['config'],candidate_config=novel[1]['config'],confirmation_seeds=plan['confirmation_seeds'])
                pr.dump(folder/(safe+'_frozen.json'),frozen)
                confirm=[]
                for role,cfg in [('anchor',frozen['anchor_config']),('candidate',frozen['candidate_config'])]:
                    rank,trial,agents=assess(family,cfg,plan['confirmation_seeds'],train,val,prepared,store/'forecast_cache',plan['policy_epochs'],plan['forecast_epochs'])
                    trial.update(role=role,stage='confirmation');confirm.append((rank,trial,agents))
                    pr.dump(folder/(safe+'_confirmation.json'),[r[1] for r in confirm])
                decision=pc.confirmation_decision(confirm[0],confirm[1]);chosen=confirm[1] if decision['accepted'] else confirm[0]
                save_models(folder/(safe+'_models'),family,plan['confirmation_seeds'],chosen[2])
                result=dict(fold=fi,family=family,screening=[r[1] for r in screen],confirmation=[r[1] for r in confirm],
                    decision=decision,selected=chosen[1],seconds=time.time()-start)
                pr.dump(checkpoint,result);completed.append(result);updates.append(decision['accepted'])
                progress=dict(round=rid,time=pc.stamp(),completed_pairs=len(completed),total_pairs=len(families)*len(splits),last_fold=fi,last_family=family,
                              process_seconds=time.time()-process_start)
                pr.dump(folder/'progress.json',progress)
                print(f'  fold {fi+1}, {family}: {len(completed)}/{progress["total_pairs"]} pairs, wins={decision["seed_wins"]}/{decision["seed_count"]}, update={decision["accepted"]}, {result["seconds"]:.1f}s',flush=True)
                # Safe resume between family/fold pairs; no partial round is a ranking.
                if not args.smoke and time.time()-process_start>args.max_seconds and len(completed)<len(families)*len(splits):
                    pr.dump(state_path,state);render_report(store);print('CHECKPOINT: round incomplete; resume this same command.',flush=True);return
        for result in completed:
            state['best'][f'{result["fold"]}:{result["family"]}']=dict(config=result['selected']['config'],delta=result['selected']['delta'],round=rid)
        state['completed_rounds']+=1;state['current_round']=None;state['status']='active';state['updated']=pc.stamp()
        state['no_improvement_rounds']=0 if any(updates) else state['no_improvement_rounds']+1
        record=dict(round=rid,time=pc.stamp(),families=len(families),folds=len(splits),screening_configurations=sum(len(r['screening']) for r in completed),
                    confirmation_configurations=sum(len(r['confirmation']) for r in completed),updates=sum(updates),
                    policy_fit_count=sum((len(r['screening'])*len(plan['screening_seeds'])+2*len(plan['confirmation_seeds'])) for r in completed if r['family']!='Rule'),
                    seconds=sum(r['seconds'] for r in completed))
        state['history'].append(record);pr.dump(folder/'complete.json',record);pr.dump(state_path,state)
        render_report(store)
        print(f'COMPLETE inclusive round {rid}: {record}',flush=True)
    finally:lock.unlink(missing_ok=True)

def render_report(store):
    import model_policy_sweep_report as report
    report.generate(store)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--store',default=str(STORE));p.add_argument('--candidates',type=int,default=4)
    p.add_argument('--policy-epochs',type=int,default=12);p.add_argument('--forecast-epochs',type=int,default=8)
    p.add_argument('--max-seconds',type=int,default=1800);p.add_argument('--smoke',action='store_true');args=p.parse_args()
    if args.candidates<2:p.error('Need incumbent plus at least one new candidate')
    if args.smoke and Path(args.store)==STORE:p.error('Smoke must use a distinct store')
    torch.set_num_threads(2);torch.use_deterministic_algorithms(True);run(args)
