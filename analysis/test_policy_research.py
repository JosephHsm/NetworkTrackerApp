"""Small deterministic checks for environment accounting and leakage boundaries."""
import unittest
from types import SimpleNamespace
import numpy as np
import torch
import ho_review as hr
import policy_research as pr

class PolicyChecks(unittest.TestCase):
    def session(self):
        a,b=(100,1),(100,2)
        return SimpleNamespace(act='car',pred=np.zeros(5),fr=[
            (0,a,{a:-90,b:-95},True),
            (5,a,{a:-90,b:-85},True),
            (10,b,{a:-80,b:-90},True),
            (15,b,{b:-100},True),
            (20,b,{b:-106},True)])
    def test_matches_legacy_a3(self):
        s=self.session();a=pr.replay(s,lambda x:True);b=hr.replay(s.fr)
        for k in pr.FIELDS:self.assertAlmostEqual(a[k],b[k],msg=k)
    def test_discount_and_terminal(self):
        s=self.session();r,tr=pr.replay(s,lambda x:True,collect=True)
        self.assertEqual(len(tr),2)
        self.assertAlmostEqual(tr[0][2],-1)
        self.assertAlmostEqual(tr[0][4],pr.GAMMA)
        # Both second and forced third move return to a cell left <30s ago.
        self.assertAlmostEqual(tr[1][2],-4-4*pr.GAMMA-5*pr.GAMMA**2)
        self.assertTrue(tr[-1][-1]);self.assertEqual(r['forced'],1)
    def test_disjoint_and_exactly_once(self):
        ss=[SimpleNamespace(act=['car','subway','walking'][i%3]) for i in range(31)]
        sp=pr.splits(ss,5,271828)
        self.assertEqual(sorted(i for s in sp for i in s['test']),list(range(31)))
        for s in sp:
            for a,b in [('train','validation'),('train','test'),('validation','test')]:
                self.assertFalse(set(s[a])&set(s[b]))
    def test_numpy_inference_matches_torch(self):
        torch.manual_seed(8);m=pr.net();x=np.random.default_rng(8).normal(size=(20,10)).astype('float32')
        np.testing.assert_allclose(pr.frozen(m)(x),m(torch.from_numpy(x)).detach().numpy(),atol=1e-6)
    def test_state_range(self):
        ids={pr.discrete(pr.features(s,m,r,d,0,'car')) for s in [-110,-100,-90,-80] for m in [4,8,12] for r in [0,1] for d in [5,20,40]}
        self.assertEqual(ids,set(range(72)))

if __name__=='__main__':unittest.main()
