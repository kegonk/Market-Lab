"""v0.8 regression: holdout, fixed-tape rule proxies, resumable integrity."""
from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest

from market_lab.research_v08 import (
    DEFAULT_HORIZONS, _canonical, _digest, execute, make_plan, parse_horizons, progress_status,
    run_arm, shadow_rules, verify_plan,
)


class TestV08Plan(unittest.TestCase):
    def test_default_is_unseen_holdout_with_three_horizons(self):
        plan = make_plan()
        self.assertEqual(plan['seeds'], list(range(101, 121)))
        self.assertEqual(plan['horizons'], list(DEFAULT_HORIZONS))
        self.assertEqual(plan['role'], 'PREDECLARED_HOLDOUT')
        verify_plan(plan)
        self.assertEqual(plan, make_plan())

    def test_no_previous_seeds_duplicate_or_out_of_range(self):
        for seeds in ((1, 101), (42, 101), (100, 101), (101, 101), (101,), (101, 2**31+1)):
            with self.subTest(seeds=seeds), self.assertRaises(ValueError):
                make_plan(seeds, (40,))
        for horizons in ((320,320),(19,), (2001,), (), (320,)*6):
            with self.subTest(horizons=horizons), self.assertRaises(ValueError):
                make_plan((101,102), horizons)
        self.assertEqual(parse_horizons('320,640,1280'), (320,640,1280))

    def test_plan_tamper_rejected_even_after_hash_recomputed(self):
        original = make_plan((101,102),(40,))
        changed = json.loads(json.dumps(original))
        changed['settings']['taker_fee_bps'] = 99
        with self.assertRaisesRegex(ValueError, 'HASH'):
            verify_plan(changed)
        changed['sha256'] = _digest({k:v for k,v in changed.items() if k!='sha256'})
        with self.assertRaisesRegex(ValueError, 'CANONICAL'):
            verify_plan(changed)


class TestV08FixedPriceShadow(unittest.TestCase):
    def test_every_strategy_same_tape_and_deterministic(self):
        prices = [1000 + ((i*7)%21) - 10 for i in range(81)]
        first = shadow_rules(prices,101)
        self.assertEqual(first,shadow_rules(prices,101))
        self.assertEqual({x['strategy'] for x in first},{'NOISE','MAKER','TREND','REVERSION'})
        self.assertEqual(len({x['tape_sha256'] for x in first}),1)
        self.assertEqual(len({x['opportunities'] for x in first}),1)
        self.assertTrue(all(x['fees_vcu'] >= 0 for x in first))
        with self.assertRaises(ValueError):
            shadow_rules([1]*20,101)
        self.assertEqual(prices[0],990)  # verify the supplied input was never modified

    def test_higher_fees_never_improve_shadow_same_path(self):
        # Changing fees can block trades, so individual strategy total score
        # need not be monotonic; use a flat tape with identical affordable fills.
        prices = [1000]*81
        cheap = shadow_rules(prices,101,maker_fee_bps=0,taker_fee_bps=0)
        costly = shadow_rules(prices,101,maker_fee_bps=10,taker_fee_bps=20)
        for a,b in zip(cheap,costly):
            self.assertLessEqual(b['vs_hold_vcu'], a['vs_hold_vcu'])
            self.assertEqual(a['fills'], b['fills'])


class TestV08Integrated(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan = make_plan((101,102),(40,))
        cls.frozen = run_arm(cls.plan,101,40,'frozen')
        cls.adaptive = run_arm(cls.plan,101,40,'adaptive')

    def test_pair_alignment_and_conservation(self):
        a,b=self.frozen,self.adaptive
        self.assertEqual(a['initial_fingerprint'], b['initial_fingerprint'])
        self.assertEqual(a['pre_review_fingerprint'], b['pre_review_fingerprint'])
        self.assertEqual(a['all_wallets'],1000)
        self.assertEqual(b['all_wallets'],1000)
        self.assertTrue(a['conservation'] and b['conservation'])
        self.assertEqual(len(a['price_tape_ticks']),41)
        self.assertIsNone(b['price_tape_ticks'])

    def test_full_cohort_inclusive_no_survivorship_exclusion(self):
        for arm in (self.frozen,self.adaptive):
            self.assertEqual(sum(x['count'] for x in arm['cohorts']),1000)
            self.assertEqual(sum(x['count'] for x in arm['cohorts'] if x['cohort'].startswith('original_')),900)
            self.assertEqual(sum(x['count'] for x in arm['cohorts'] if 'reserve' in x['cohort']),100)

    def test_first_switch_panel_censored_or_observed_and_unique(self):
        events=self.adaptive['first_switches']
        self.assertEqual(len(events),len({x['agent'] for x in events}))
        for e in events:
            self.assertEqual(e['followup_tick'],e['first_switch_tick']+20)
            if e['censored']:
                self.assertIsNone(e['after_vs_hold_vcu'])
                self.assertGreater(e['followup_tick'],40)
            else:
                self.assertAlmostEqual(e['after_vs_hold_vcu']-e['before_vs_hold_vcu'],e['change_vs_hold_vcu'],places=5)
        self.assertEqual(self.frozen['first_switches'], [])

    def test_recovery_refuses_modified_checkpoint_and_reuses_good_one(self):
        with tempfile.TemporaryDirectory() as td:
            out=Path(td)/'research'
            with redirect_stdout(io.StringIO()) as capture:
                self.assertIsNone(execute(self.plan,out,max_new_arms=1))
            self.assertIn('RESEARCH_PAUSED=YES',capture.getvalue())
            self.assertEqual(progress_status(self.plan,out)['verified'],1)
            first = next((out/'checkpoints').glob('*.json'))
            check = json.loads(first.read_text('utf-8'))
            check['data']['all_wallets']=999
            first.write_bytes(_canonical(check))
            with self.assertRaisesRegex(ValueError,'INTEGRITY'):
                execute(self.plan,out,resume=True)

    def test_interrupted_matches_uninterrupted_and_final_files_verified(self):
        with tempfile.TemporaryDirectory() as td:
            out=Path(td)/'resumed'
            with redirect_stdout(io.StringIO()):
                execute(self.plan,out,max_new_arms=1)
                resumed=execute(self.plan,out,resume=True)
                existing=execute(self.plan,out,resume=True)
            self.assertEqual(existing,resumed)
            with self.assertRaises(FileExistsError):
                execute(self.plan,out)
            with self.assertRaises(ValueError):
                execute(make_plan((103,104),(40,)),out,resume=True)
            other=Path(td)/'fresh'
            with redirect_stdout(io.StringIO()):
                uninterrupted=execute(self.plan,other)
            self.assertEqual(resumed,uninterrupted)
            for name in ('summary.json','runs.csv','paired.csv','statistics.csv','cohorts.csv',
                         'agent_changes.csv','shadow.csv','shadow_statistics.csv','report.html','complete.json'):
                self.assertEqual((out/name).read_bytes(), (other/name).read_bytes(),name)
            self.assertEqual(len(resumed['runs']),4)
            self.assertEqual(len(resumed['pairs']),2)
            self.assertEqual(len(resumed['shadow']),8)
            self.assertEqual(sum(x['count'] for x in resumed['cohorts'] if x['seed']==101 and x['arm']=='adaptive'),1000)
            self.assertIn('simulated only', (out/'report.html').read_text(encoding='utf-8').lower())
            self.assertNotIn('http://', (out/'report.html').read_text(encoding='utf-8'))
            self.assertNotIn('https://', (out/'report.html').read_text(encoding='utf-8'))
            (out/'paired.csv').write_text('corrupt',encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'FINAL_HASH_MISMATCH'):
                execute(self.plan,out,resume=True)


if __name__ == '__main__':
    unittest.main()
