import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from katana_regression import aggregate
from katana_effective_compare import MODES, write_json
from crawler_contract_benchmark import contract_index


class RegressionAcceptanceTests(unittest.TestCase):
    def fixture(self, root, rounds, unstable=False):
        for n in range(1, rounds+1):
            site=root/f'round-{n:02}'/'fixture'
            for mode in MODES:
                rows=[{'method':'GET','url':'https://example.test/a'}]
                if unstable and n==2 and mode=='basecrawler':rows=[{'method':'GET','url':'https://example.test/b'}]
                write_json(site/mode/'candidates.json',rows)
            write_json(site/'effective.json',{'passed':True,'coverage':1.,'verified_valid_reference':1,'unresolved':[],'missing':[],
                'runs':{m:{'seconds':float(n),'status':'completed'} for m in MODES}})

    def test_single_round_cannot_certify_three_round_stability(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);self.fixture(root,1)
            self.assertFalse(aggregate(root,[SimpleNamespace(site_id='fixture')],1)['goal_met'])

    def test_instability_blocks_acceptance_even_with_full_coverage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);self.fixture(root,3,unstable=True)
            result=aggregate(root,[SimpleNamespace(site_id='fixture')],3)
            self.assertFalse(result['goal_met'])
            self.assertEqual(result['sites'][0]['modes']['basecrawler']['min_jaccard'],0)

    def test_stable_three_rounds_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);self.fixture(root,3)
            self.assertTrue(aggregate(root,[SimpleNamespace(site_id='fixture')],3)['goal_met'])

    def test_contract_methods_are_not_merged(self):
        self.assertNotEqual(contract_index('POST','http://localhost/api/widgets/3'),contract_index('GET','http://localhost/api/widgets/3'))
        self.assertIsNone(contract_index('GET','http://localhost/api/form'))


if __name__=='__main__':unittest.main()
