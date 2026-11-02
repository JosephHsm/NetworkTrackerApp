import tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
import pandas as pd
import torch
import model_policy_sweep as m

def session(name,rs):
    n=len(rs)
    return SimpleNamespace(name=name,df=pd.DataFrame(dict(rsrp_dbm=rs,timestamp=np.arange(n)*5000,cell_info_age_ms=np.zeros(n))),fr=[None]*n)

class ModelPolicyTests(unittest.TestCase):
    def test_future_signal_never_changes_past_input(self):
        a=session('a',np.linspace(-90,-70,24));x,_=m.forecast_data([a]);a.df.loc[20:,'rsrp_dbm']=-130
        y,_=m.forecast_data([a]);np.testing.assert_array_equal(x['a'][:20],y['a'][:20])
    def test_initial_missing_values_are_not_future_filled(self):
        a=session('a',[np.nan,-70,-60,-60]);x,_=m.forecast_data([a])
        self.assertTrue(np.allclose(x['a'][0,:,0],(-105+90)/20))
    def test_forecaster_fit_uses_only_train_rows(self):
        train=session('train',np.linspace(-95,-80,24));val=session('val',np.linspace(-60,-40,24))
        prepared=m.forecast_data([train,val]);seen={}
        class Fake:
            def __init__(self,**kw):pass
            def fit(self,x,y):seen['x']=x.copy();seen['y']=y.copy()
            def save_model(self,path):pass
            def predict(self,x):return np.zeros(len(x))
        scratch=m.OUT.parent/'work';scratch.mkdir(parents=True,exist_ok=True)
        d=Path(tempfile.mkdtemp(prefix='forecast_leakage_',dir=scratch))
        with patch('xgboost.XGBRegressor',Fake):
            result=m.fit_forecast('xgboost',m.default_config('DQN+XGBoost')['forecast'],3,[train],[val],prepared,d,1)
        x,t=prepared;want=x['train'][t['train'][1]]
        np.testing.assert_array_equal(seen['x'],want.reshape(len(want),-1))
        np.testing.assert_array_equal(seen['y'],t['train'][0][t['train'][1]]/10)
        self.assertEqual(result['training_rows'],len(want))
    def test_initial_dqn_control_settings_match(self):
        baseline=m.candidates('DQN',m.default_config('DQN'),1)[0]
        for f in m.PREDICTORS:
            candidate=m.candidates(f,m.default_config(f),1)[0]
            self.assertEqual(baseline,candidate)
    def test_replay_instrumentation_restores_on_training_error(self):
        original=m.pr.replay
        with patch.object(m.TunedTable,'train',side_effect=RuntimeError('test failure')):
            with self.assertRaises(RuntimeError):m.assess('Q',m.default_config('Q'),[1],[],[],({},{}),Path('.'),1,1)
        self.assertIs(m.pr.replay,original)

if __name__=='__main__':unittest.main()
