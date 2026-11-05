import unittest
import policy_continue as c

def result(utilities,seeds=(47,83,101,137,173)):
    def delta(u):return dict(ho_reduction=u,pp_reduction=u,weak_delta_pp=-.1,rsrp_loss=.1)
    d=delta(sum(utilities)/len(utilities))
    return c.rank_delta(d),dict(seed_results=[dict(seed=s,delta=delta(u)) for s,u in zip(seeds,utilities)]),[]

class ConfirmationTests(unittest.TestCase):
    def test_mean_gain_without_majority_is_rejected(self):
        a=result([.4]*5);b=result([1.,1.,.3,.3,.3])
        d=c.confirmation_decision(a,b)
        self.assertTrue(d['mean_improved']);self.assertEqual(d['seed_wins'],2);self.assertFalse(d['accepted'])
    def test_mean_gain_and_majority_are_accepted(self):
        d=c.confirmation_decision(result([.4]*5),result([.5,.5,.5,.3,.3]))
        self.assertEqual(d['seed_wins'],3);self.assertTrue(d['accepted'])
    def test_tiny_gain_is_rejected(self):
        self.assertFalse(c.confirmation_decision(result([.4]*5),result([.401]*5))['accepted'])
    def test_unpaired_seeds_rejected(self):
        with self.assertRaises(ValueError):c.confirmation_decision(result([.4]*5),result([.5]*5,seeds=(1,2,3,4,5)))
    def test_new_seed_sets_never_overlap(self):
        previous=set(c.SEEDS)
        for batch in range(1,100):
            now={batch*10000+s for s in c.CONFIRM_SEED_BASES}
            self.assertEqual(len(now),5);self.assertFalse(previous&now);previous|=now

if __name__=='__main__':unittest.main()
