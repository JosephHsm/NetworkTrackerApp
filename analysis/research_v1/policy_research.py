"""Nested session policy study. Run from any directory; no external API calls.

python analysis/policy_research.py --run-name policy_v2
Each outer test session is used only after validation selection. CQL uses a fixed
SYNTHETIC replay buffer, not logged real-network action/reward transitions.
"""
from __future__ import annotations
import argparse, copy, hashlib, itertools, json, platform, time
from pathlib import Path
import numpy as np
import pandas as pd
import torch
from torch import nn
import ho_review as hr

ROOT = Path(__file__).resolve().parents[1]
GAMMA = .99  # discount per observed ROW, not per decision or second
FIELDS = ('ho', 'pp', 'forced', 'rows', 'weak105', 'rsrp_sum', 'gap_sum')

def dump(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=lambda x: x.item() if isinstance(x, np.generic) else str(x)), encoding='utf-8')
    tmp.replace(path)

def aggregate(items):
    a = {k: sum(r[k] for r in items) for k in FIELDS}
    n = max(a['rows'], 1)
    return dict(a, weak_share=a['weak105']/n, mean_rsrp=a['rsrp_sum']/n, gap=a['gap_sum']/n)

def delta(r, b):
    return dict(ho_reduction=1-r['ho']/max(b['ho'], 1), pp_reduction=1-r['pp']/max(b['pp'], 1),
                weak_delta_pp=100*(r['weak_share']-b['weak_share']), rsrp_loss=b['mean_rsrp']-r['mean_rsrp'],
                gap=r['gap'], forced=r['forced'])

def selection(r, b):
    """Predeclared constrained scalarization, identical for all model families.
    Feasible: weak-row share does not rise and mean RSRP loss <=1 dB.
    If no feasible policy, minimize violations before HO/PP utility.
    """
    d = delta(r, b)
    violation = max(d['weak_delta_pp'], 0) + max(d['rsrp_loss']-1, 0)
    utility = .5*d['ho_reduction'] + .5*d['pp_reduction']
    return (violation <= 1e-12, -violation, utility)

def features(s, margin, ret, dwell, ho30, act, trend=0., forecast=0.):
    return np.array([(s+90)/20, margin/15, float(ret), min(dwell,300)/60,
                     ho30/5, act=='car', act=='subway', act=='walking', trend/10, forecast/10], dtype=np.float32)

def discrete(x):
    s = x[0]*20-90; m = x[1]*15; dwell = x[3]*60
    return (int(np.searchsorted([-105,-95,-85],s,side='left'))*3 + int(np.searchsorted([6,10],m,side='right')))*6 + int(x[2])*3 + int(np.searchsorted([10,30],dwell,side='right'))

class Session:
    def __init__(self, path):
        self.name = path.name; self.act = hr.activity(str(path))
        self.df = hr.load(str(path)); self.fr = hr.frames(self.df)
        self.pred = np.zeros(len(self.fr), dtype=np.float32)
        self.base = {k:v for k,v in hr.replay(self.fr,network=True).items() if k in FIELDS}

def replay(session, policy, reward=(3.,5.,.1), hyst=3., collect=False):
    """Decision-to-decision SMDP transitions; accumulated rewards and gamma^rows.
    No future row is exposed to policy. Forced moves stay in rewards and totals.
    Freshness/coverage matches legacy hr.replay; weak_share is ROW-weighted.
    """
    cur = None; left=[]; hist=[]; last_t=0.; prev_s=None
    totals = dict.fromkeys(FIELDS,0); transitions=[]; pending=None
    wpp, wweak, wgap = reward
    for j,(t,sk,m,fresh) in enumerate(session.fr):
        if cur is None: cur=sk; last_t=t
        new=None
        if cur not in m:
            new=sk; totals['forced']+=1
        elif fresh:
            s=m[cur]
            best=max((k for k in m if k !=cur and k[0]==cur[0]),key=m.get,default=None)
            if best is not None and m[best]-s >=hyst:
                ret=any(k==best and t-ts<hr.PP_S for k,ts in left)
                x=features(s,m[best]-s,ret,t-last_t,sum(t-ts<30 for ts in hist),session.act,
                           0 if prev_s is None else s-prev_s,session.pred[j])
                if pending is not None:
                    transitions.append((*pending[:3],x,pending[3],False))
                a=int(policy(x)); pending=[x,a,0.,1.]
                if a: new=best
            prev_s=s
        r=0.
        if new is not None and new !=cur:
            totals['ho']+=1; r-=1
            if any(k==new and t-ts<hr.PP_S for k,ts in left):
                totals['pp']+=1; r-=wpp
            left=[(cur,t)]+[(k,ts) for k,ts in left if t-ts<hr.PP_S]
            hist=[ts for ts in hist if t-ts<30]+[t]
            cur=new; last_t=t
        if fresh and cur in m:
            s=m[cur]; g=max(v for k,v in m.items() if k[0]==cur[0])-s
            totals['rows']+=1; totals['rsrp_sum']+=s; totals['gap_sum']+=g
            weak=int(s<=-105); totals['weak105']+=weak
            r-=wweak*weak+wgap*min(g,15)
        if pending is not None:
            pending[2]+=pending[3]*r; pending[3]*=GAMMA
    if pending is not None: transitions.append((*pending[:3],np.zeros_like(pending[0]),pending[3],True))
    return (totals,transitions) if collect else totals

class Table:
    def __init__(self, seed=0):
        self.q=np.zeros((72,2)); self.rng=np.random.default_rng(seed); self.eps=0.
    def __call__(self,x):
        return self.rng.integers(2) if self.rng.random()<self.eps else self.q[discrete(x)].argmax()
    def train(self,sessions,reward,epochs):
        for ep in range(epochs):
            self.eps=max(.02,.3*(1-ep/max(epochs*.7,1)))
            for i in self.rng.permutation(len(sessions)):
                _,tr=replay(sessions[i],self,reward,collect=True)
                for x,a,r,n,d,done in tr:
                    k=discrete(x)
                    target=r+(0 if done else d*self.q[discrete(n)].max())
                    self.q[k,a]+=.1*(target-self.q[k,a])
        self.eps=0.; return self

def net(outputs=2):
    return nn.Sequential(nn.Linear(10,64),nn.Tanh(),nn.Linear(64,64),nn.Tanh(),nn.Linear(64,outputs))

def frozen(model):
    """Fast numpy inference, numerically identical to the torch tanh MLP."""
    p=[v.detach().numpy().copy() for v in model.parameters()]
    return lambda x: np.tanh(np.tanh(x@p[0].T+p[1])@p[2].T+p[3])@p[4].T+p[5]

def tensors(tr):
    x,a,r,n,d,done=zip(*tr)
    return (torch.tensor(np.stack(x)),torch.tensor(a,dtype=torch.long),torch.tensor(r,dtype=torch.float32),
            torch.tensor(np.stack(n)),torch.tensor(d,dtype=torch.float32),torch.tensor(done,dtype=torch.float32))

class Neural:
    def __init__(self,kind,seed,gru=False):
        torch.manual_seed(seed); self.rng=np.random.default_rng(seed)
        self.kind=kind; self.gru=gru; self.model=net(); self.target=copy.deepcopy(self.model)
        self.value=net(1); self.opt=torch.optim.Adam(list(self.model.parameters())+(list(self.value.parameters()) if kind=='PPO' else []),lr=3e-4)
        self.curve=[]; self.sync()
    def sync(self): self.forward=frozen(self.model)
    def input(self,x):
        if self.gru:return x
        y=x.copy(); y[9]=0.; return y
    def __call__(self,x): return self.forward(self.input(x)).argmax()
    def train(self,sessions,reward,epochs,alpha=0.):
        buffer=[]
        if self.kind=='CQL':
            # Frozen mixed behavior dataset generated ONLY from inner training sessions.
            for s in sessions:
                for offset in (0.,3.,6.):
                    def behavior(x):
                        a=int(x[1]*15 >= 3+offset+6*x[2])
                        return int(self.rng.integers(2)) if self.rng.random()<.15 else a
                    buffer.extend(replay(s,behavior,reward,collect=True)[1])
        for ep in range(epochs):
            self.sync(); fresh=[]
            if self.kind!='CQL':
                for si in self.rng.permutation(len(sessions)):
                    def act(x):
                        z=self.forward(self.input(x))
                        if self.kind=='PPO':
                            p=np.exp(z-z.max());p/=p.sum();return self.rng.choice(2,p=p)
                        eps=max(.05,.4*(1-ep/max(epochs*.8,1)))
                        return self.rng.integers(2) if self.rng.random()<eps else z.argmax()
                    fresh.extend(replay(sessions[si],act,reward,collect=True)[1])
            if self.kind=='PPO':
                if not fresh:continue
                x,a,r,n,d,done=tensors(fresh)
                if not self.gru:x[:,9]=0;n[:,9]=0
                with torch.no_grad():
                    old=nn.functional.log_softmax(self.model(x),-1).gather(1,a[:,None]).squeeze(1)
                    val=self.value(x).squeeze(1); nv=self.value(n).squeeze(1)
                    td=r+d*(1-done)*nv-val
                    adv=torch.zeros_like(r); tail=0.
                    for k in reversed(range(len(r))):
                        tail=td[k]+d[k]*.95*(1-done[k])*tail;adv[k]=tail
                    ret=adv+val; adv=(adv-adv.mean())/(adv.std(unbiased=False)+1e-6)
                for _ in range(4):
                    for idx in torch.randperm(len(x)).split(256):
                        logits=self.model(x[idx]); logp=nn.functional.log_softmax(logits,-1)
                        ratio=(logp.gather(1,a[idx,None]).squeeze(1)-old[idx]).exp()
                        loss=-torch.minimum(ratio*adv[idx],ratio.clamp(.8,1.2)*adv[idx]).mean()
                        loss+=.5*nn.functional.mse_loss(self.value(x[idx]).squeeze(1),ret[idx])
                        loss+=.01*(logp.exp()*logp).sum(-1).mean()
                        self.opt.zero_grad();loss.backward();nn.utils.clip_grad_norm_(list(self.model.parameters())+list(self.value.parameters()),.5);self.opt.step()
            else:
                buffer.extend(fresh); buffer=buffer[-50000:]
                if not buffer:continue
                for _ in range(max(20,min(80,len(buffer)//128))):
                    idx=self.rng.integers(len(buffer),size=min(256,len(buffer)))
                    x,a,r,n,d,done=tensors([buffer[k] for k in idx])
                    if not self.gru:x[:,9]=0;n[:,9]=0
                    q=self.model(x);qa=q.gather(1,a[:,None]).squeeze(1)
                    with torch.no_grad():
                        na=self.model(n).argmax(-1)
                        target=r+d*(1-done)*self.target(n).gather(1,na[:,None]).squeeze(1)
                    loss=nn.functional.smooth_l1_loss(qa,target)+alpha*(q.logsumexp(-1)-qa).mean()
                    self.opt.zero_grad();loss.backward();nn.utils.clip_grad_norm_(self.model.parameters(),5);self.opt.step()
                if (ep+1)%2==0:self.target.load_state_dict(self.model.state_dict())
            self.curve.append(float(loss.detach()))
        self.sync();return self

def fit_forecast(train,all_sessions,seed,epochs=15):
    """GRU predicts observed serving-RSRP delta at >=10s, <=12.5s.
    It is an observation-stream predictor, not a counterfactual virtual-cell predictor.
    No bfill or future values in input; train split only for weights/scaling.
    """
    torch.manual_seed(seed)
    class Forecast(nn.Module):
        def __init__(self):
            super().__init__();self.gru=nn.GRU(2,16,batch_first=True);self.fc=nn.Linear(16,1)
        def forward(self,x):return self.fc(self.gru(x)[0][:,-1]).squeeze(1)
    windows={};targets={}
    for s in all_sessions:
        d=s.df; rs=pd.to_numeric(d.rsrp_dbm,errors='coerce').ffill().fillna(-105).to_numpy(float)
        t=d.timestamp.to_numpy()/1000.; age=d.cell_info_age_ms.to_numpy(float)
        inp=np.column_stack(((rs+90)/20,(~(age<=8000)).astype(float)))
        padded=np.pad(inp,((11,0),(0,0)),mode='edge')
        windows[s.name]=np.stack([padded[j:j+12] for j in range(len(rs))]).astype('float32')
        k=np.searchsorted(t,t+10);ok=k<len(t);kc=np.minimum(k,len(t)-1)
        ok&=(t[kc]-t<=12.5)&(age<=8000)&(age[kc]<=8000)
        y=(rs[kc]-rs)/10;targets[s.name]=(y.astype('float32'),ok)
    X=np.concatenate([windows[s.name][targets[s.name][1]] for s in train]);Y=np.concatenate([targets[s.name][0][targets[s.name][1]] for s in train])
    model=Forecast();opt=torch.optim.Adam(model.parameters(),lr=.002)
    for _ in range(epochs):
        for idx in torch.randperm(len(X)).split(256):
            loss=nn.functional.l1_loss(model(torch.from_numpy(X[idx])),torch.from_numpy(Y[idx]))
            opt.zero_grad();loss.backward();opt.step()
    model.eval()
    with torch.no_grad():
        for s in all_sessions:
            s.pred=np.concatenate([model(torch.from_numpy(x)).numpy()*10 for x in np.array_split(windows[s.name],max(1,len(s.fr)//512))])
    return model

def evaluate(sessions,policy,reward=(3.,5.,.1),hyst=3.):
    return [replay(s,policy,reward,hyst) for s in sessions]

def splits(sessions,k,seed):
    # Activity-stratified outer assignment; round-robin offset avoids empty folds.
    rng=np.random.default_rng(seed);groups=[[] for _ in range(k)];offset=0
    for act in sorted({s.act for s in sessions}):
        ids=[i for i,s in enumerate(sessions) if s.act==act];rng.shuffle(ids)
        for j,i in enumerate(ids):groups[(offset+j)%k].append(i)
        offset=(offset+len(ids))%k
    result=[]
    for f,test in enumerate(groups):
        remain=[i for i in range(len(sessions)) if i not in test];val=[]
        for act in sorted({s.act for s in sessions}):
            ids=[i for i in remain if sessions[i].act==act];rng.shuffle(ids)
            if len(ids)>=3:val.extend(ids[:max(1,round(len(ids)*.25))])
        train=[i for i in remain if i not in val]
        assert not(set(train)&set(val) or set(train)&set(test) or set(val)&set(test))
        result.append(dict(fold=f,train=train,validation=val,test=test))
    assert sorted(i for r in result for i in r['test'])==list(range(len(sessions)))
    return result

def bootstrap(records,seeds,reps=2000):
    """Paired session cluster bootstrap after averaging seed metrics per session.
    Seed variation is separately reported; this CI conditions on fitted folds.
    """
    out={};rng=np.random.default_rng(20261101)
    names=sorted({r['policy'] for r in records});ids=sorted({r['session'] for r in records})
    per={}
    for name in names:
        per[name]=[]
        for sid in ids:
            rr=[r['metrics'] for r in records if r['policy']==name and r['session']==sid]
            assert rr,(name,sid)
            per[name].append({k:float(np.mean([r[k] for r in rr])) for k in FIELDS})
    for name in names:
        center=aggregate(per[name]);base=aggregate(per['Network'])
        vals=[]
        for _ in range(reps):
            idx=rng.integers(len(ids),size=len(ids))
            vals.append(delta(aggregate([per[name][i] for i in idx]),aggregate([per['Network'][i] for i in idx])))
        ci={k:np.quantile([v[k] for v in vals],[.025,.975]).tolist() for k in vals[0]}
        by_seed=[]
        for seed in seeds:
            rr=[r['metrics'] for r in records if r['policy']==name and r['seed'] in (-1,seed)]
            by_seed.append(delta(aggregate(rr),base))
        out[name]=dict(metrics=center,delta=delta(center,base),ci95=ci,seed_results=by_seed)
    return out

def main():
    p=argparse.ArgumentParser();p.add_argument('--run-name',default='policy_v2');p.add_argument('--folds',type=int,default=5)
    p.add_argument('--seeds',type=int,nargs='+',default=[0,1,2]);p.add_argument('--search-epochs',type=int,default=15)
    p.add_argument('--epochs',type=int,default=50);p.add_argument('--neural-epochs',type=int,default=30)
    p.add_argument('--bootstrap',type=int,default=2000);p.add_argument('--smoke',action='store_true')
    args=p.parse_args();torch.set_num_threads(2);torch.use_deterministic_algorithms(True)
    out=ROOT/'analysis_output'/args.run_name
    if out.exists(): raise SystemExit(f'Refusing to overwrite existing run: {out}')
    out.mkdir(parents=True);start=time.time()
    files=sorted((ROOT/'trackingcsv/new').glob('*.csv'));sessions=[Session(f) for f in files]
    split=splits(sessions,args.folds,271828)
    manifest=dict(args=vars(args),data=[dict(file=f.name,sha256=hashlib.sha256(f.read_bytes()).hexdigest(),rows=len(s.df),activity=s.act) for f,s in zip(files,sessions)],
                  splits=[{k:([sessions[i].name for i in v] if isinstance(v,list) else v) for k,v in r.items()} for r in split],
                  code_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),python=platform.python_version(),torch=torch.__version__,numpy=np.__version__,pandas=pd.__version__,gamma=GAMMA,
                  selection='lexicographic feasibility(weak_delta<=0, mean_RSRP_loss<=1dB), minimum violation, 0.5*HO_reduction+0.5*PP_reduction',
                  caveats=['Previously explored corpus: nested OOF is not a newly collected external test.', 'CQL buffer is synthetic mixed replay behavior, not real logged actions.', 'Weak share is fresh-row weighted, not duration weighted.', 'Paired CI resamples sessions after seed averaging, conditional on fitted folds; overlapping routes/days are not independent subjects.', 'Only intra-frequency observed candidate cells; no real network feedback.'])
    dump(out/'manifest.json',manifest)
    records=[];search=[];choices=[];curves=[]
    rewards=list(itertools.product((1.,3.,9.),(2.,5.,15.),(.03,.1,.3)))
    rules=list(itertools.product((3.,6.,9.),(0.,3.,6.),(None,-95.,-90.)))
    if args.smoke:rewards=rewards[:2];rules=rules[:2]
    for sp in split:
        fi=sp['fold'];train=[sessions[i] for i in sp['train']];val=[sessions[i] for i in sp['validation']];test=[sessions[i] for i in sp['test']]
        base=aggregate([s.base for s in val])
        print(f'Fold {fi+1}/{args.folds}: train={len(train)}, val={len(val)}, test={len(test)}',flush=True)
        rule_candidates=[]
        for hyst,pen,a2 in rules:
            def rule(x):return (a2 is None or x[0]*20-90<a2) and x[1]*15>=hyst+pen*x[2]
            score=aggregate(evaluate(val,rule,hyst=hyst));cfg=dict(hyst=hyst,pen=pen,a2=a2)
            rule_candidates.append((selection(score,base),cfg));search.append(dict(fold=fi,family='Rule',config=cfg,**delta(score,base)))
        cfg=max(rule_candidates,key=lambda v:v[0])[1]
        h,pen,a2=cfg['hyst'],cfg['pen'],cfg['a2']
        rule=lambda x:(a2 is None or x[0]*20-90<a2) and x[1]*15>=h+pen*x[2]
        choices.append(dict(fold=fi,family='Rule',config=cfg))
        for s in test:
            for name,res in [('Network',s.base),('A3',replay(s,lambda x:True)),('Rule',replay(s,rule,hyst=h))]:
                records.append(dict(fold=fi,session=s.name,activity=s.act,policy=name,seed=-1,metrics=res))
        candidates=[]
        for j,reward in enumerate(rewards):
            agent=Table(seed=100+fi).train(train,reward,args.search_epochs)
            score=aggregate(evaluate(val,agent,reward));candidates.append((selection(score,base),reward))
            search.append(dict(fold=fi,family='Q',config=dict(reward=reward),**delta(score,base)))
        best=max(candidates,key=lambda v:v[0])[1];choices.append(dict(fold=fi,family='Q',config=dict(reward=best)))
        print(f'  Q reward search complete: {len(rewards)} candidates, selected={best}',flush=True)
        # GRU is fitted only on train; outputs for val/test are inference only.
        forecast=fit_forecast(train,sessions,fi,epochs=2 if args.smoke else 15)
        torch.save(forecast.state_dict(),out/f'fold{fi}_forecast.pt')
        # The three top Q-validation rewards define a modest neural search budget.
        shortlist=[v[1] for v in sorted(candidates,key=lambda v:v[0],reverse=True)[:3]]
        neural_choices={}
        for family in ('DQN','DQN+GRU','CQL','PPO'):
            candidate=[]
            for reward in shortlist:
                for alpha in ((.1,1.) if family=='CQL' and not args.smoke else (0. if family!='CQL' else 1.,)):
                    agent=Neural('DQN' if family.startswith('DQN') else family,100+fi,gru=family.endswith('GRU'))
                    agent.train(train,reward,2 if args.smoke else max(5,args.neural_epochs//3),alpha)
                    score=aggregate(evaluate(val,agent,reward));cfg=dict(reward=reward,alpha=alpha)
                    candidate.append((selection(score,base),cfg));search.append(dict(fold=fi,family=family,config=cfg,**delta(score,base)))
            cfg=max(candidate,key=lambda v:v[0])[1];neural_choices[family]=cfg;choices.append(dict(fold=fi,family=family,config=cfg))
            print(f'  {family}: {len(candidate)} validation configurations done',flush=True)
        for seed in args.seeds:
            agent=Table(seed).train(train,best,args.epochs)
            np.save(out/f'fold{fi}_seed{seed}_Q.npy',agent.q)
            for s,res in zip(test,evaluate(test,agent,best)):
                records.append(dict(fold=fi,session=s.name,activity=s.act,policy='Q',seed=seed,metrics=res))
            for family,cfg in neural_choices.items():
                agent=Neural('DQN' if family.startswith('DQN') else family,seed,gru=family.endswith('GRU'))
                agent.train(train,cfg['reward'],args.neural_epochs,cfg['alpha'])
                torch.save(agent.model.state_dict(),out/f'fold{fi}_seed{seed}_{family.replace("+","_")}.pt')
                curves.append(dict(fold=fi,seed=seed,family=family,loss=agent.curve))
                for s,res in zip(test,evaluate(test,agent,cfg['reward'])):
                    records.append(dict(fold=fi,session=s.name,activity=s.act,policy=family,seed=seed,metrics=res))
            print(f'  final seed {seed} complete; elapsed {time.time()-start:.0f}s',flush=True)
        dump(out/'records.json',records);dump(out/'search.json',search);dump(out/'choices.json',choices);dump(out/'curves.json',curves)
        dump(out/'progress.json',dict(completed_folds=fi+1,total_folds=args.folds,elapsed_seconds=time.time()-start,status='running'))
    result=bootstrap(records,args.seeds,args.bootstrap)
    dump(out/'summary.json',dict(policies=result,elapsed_seconds=time.time()-start))
    dump(out/'progress.json',dict(completed_folds=args.folds,total_folds=args.folds,elapsed_seconds=time.time()-start,status='complete'))
    pd.DataFrame([dict(policy=r['policy'],seed=r['seed'],fold=r['fold'],session=r['session'],activity=r['activity'],**r['metrics']) for r in records]).to_csv(out/'session_metrics.csv',index=False,encoding='utf-8-sig')
    print(json.dumps({k:v['delta'] for k,v in result.items()},indent=2),flush=True)
    print(f'COMPLETE {out} ({time.time()-start:.0f}s)',flush=True)

if __name__=='__main__':main()
