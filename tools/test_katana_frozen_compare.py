import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from katana_frozen_compare import freeze, select_maximum, validate_reference, run, summarize


class FrozenReferenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)

    def folder(self,name,urls):
        p=self.root/name;p.mkdir(parents=True)
        (p/'candidates.json').write_text(json.dumps([{'method':'GET','url':u} for u in urls]),encoding='utf-8')
        (p/'run.json').write_text(json.dumps({'status':'completed','seconds':1}),encoding='utf-8')
        (p/'results.jsonl').write_text('',encoding='utf-8')
        return p

    def test_duplicates_do_not_outweigh_distinct_urls(self):
        a=self.folder('a',['https://x/a']*10)
        b=self.folder('b',['https://x/b','https://x/c'])
        self.assertEqual(select_maximum([a,b]),b)

    def test_freeze_keeps_one_run_instead_of_union_and_detects_tampering(self):
        source=self.root/'source';source.mkdir()
        config={'targets':'sample','seconds':60,'pages':80,'depth':2,'rate':2,'strategy':'breadth-first','proxy':'','katana_sha256':'hash'}
        (source/'config.json').write_text(json.dumps(config),encoding='utf-8')
        for mode in ['katana-static','katana-browser']:
            self.folder('source/round-01/sample/'+mode,['https://x/only-first'])
            self.folder('source/round-02/sample/'+mode,['https://x/a','https://x/b'])
        output=self.root/'frozen';freeze([source],output)
        validate_reference(output)
        rows=json.loads((output/'sample/katana-static/candidates.json').read_text(encoding='utf-8'))
        self.assertEqual({r['url'] for r in rows},{'https://x/a','https://x/b'})
        (output/'sample/katana-static/candidates.json').write_text('[]',encoding='utf-8')
        with self.assertRaises(ValueError):validate_reference(output)

    def test_existing_frozen_version_cannot_be_overwritten(self):
        with self.assertRaises(ValueError):freeze([],self.root)

    def test_run_executes_only_basecrawler(self):
        reference=self.root/'reference';reference.mkdir()
        (reference/'frozen.json').write_text('{}',encoding='utf-8')
        for mode in ['katana-static','katana-browser']:
            self.folder('reference/testasp/'+mode,['https://x/'])
        binary=self.root/'base.exe';binary.write_bytes(b'fixture')
        args=SimpleNamespace(reference=reference,targets='testasp',base=binary,output=self.root/'output',
                              rounds=1,workers=1,resume=False,verify_only=False,base_concurrency=4,base_rate_limit=9,base_seconds_limit=20,base_max_pages_per_host=300,base_depth_limit=3,
                             control_urls={'testasp':'https://testasp.net/__control_missing'})
        manifest={'sites':{'testasp':{'settings':{'katana_sha256':'fixture','seconds':60,'pages':80,'depth':2,'rate':2,'strategy':'breadth-first','proxy':''}}}}
        with patch('katana_frozen_compare.validate_reference',return_value=manifest), \
             patch('katana_frozen_compare.bench.run_one') as execute, \
             patch('katana_frozen_compare.bench.verify_site'), \
             patch('katana_frozen_compare.summarize'):
            run(args)
        self.assertEqual(execute.call_count,1)
        self.assertEqual(execute.call_args.args[1],'basecrawler')
        options=execute.call_args.args[2]
        self.assertEqual(options.base_concurrency,4)
        self.assertEqual(options.base_rate_limit,9)
        self.assertEqual(options.base_seconds_limit,20)
        self.assertEqual(options.base_max_pages_per_host,300)
        self.assertEqual(options.base_depth_limit,3)
        self.assertEqual(options.control_urls,args.control_urls)
        config=json.loads((args.output/'config.json').read_text(encoding='utf-8'))
        self.assertEqual(config['base_concurrency'],4)
        self.assertEqual(config['base_rate_limit'],9)
        self.assertEqual(config['base_seconds_limit'],20)
        self.assertEqual(config['base_max_pages_per_host'],300)
        self.assertEqual(config['base_depth_limit'],3)
        self.assertEqual(config['control_urls'],args.control_urls)

    def test_summary_separates_reference_coverage_and_crawl_completion(self):
        output = self.root / 'comparison'
        site = output / 'round-01' / 'sample'
        (site / 'basecrawler').mkdir(parents=True)
        (site / 'effective.json').write_text(json.dumps({
            'coverage': 1.0,
            'discovery_coverage': 1.0,
            'successful_reference_coverage': 0.96,
            'execution_coverage_passed': True,
            'crawl_complete': False,
            'strict_passed': False,
            'strict_failure_reasons': ['basecrawler_crawl_incomplete'],
            'passed': False,
            'missing': [],
            'runs': {
                'basecrawler': {'seconds': 180},
                'katana-static': {'seconds': 60},
                'katana-browser': {'seconds': 60},
            },
        }), encoding='utf-8')
        (site / 'basecrawler' / 'candidates.json').write_text('[]', encoding='utf-8')
        source_counts = {
            'a': {
                'discovered_method_urls': 20,
                'scheduled_method_urls': 18,
                'crawlable_method_urls': 20,
                'dispatches': 18,
                'successful_http_responses': 18,
                'exact_successful_method_urls': 18,
                'reference_targets_discovered': 10,
                'scheduled_reference_targets': 10,
                'reference_targets_attempted': 9,
                'reference_targets_successful': 9,
                'unvisited_by_reason': {'timeout': 2},
            },
        }
        (site / 'metrics.json').write_text(json.dumps({
            'modes': {'basecrawler': {
                'seconds': 180,
                'unvisited': 2,
                'discovered_method_urls': 20,
                'observed_successful_method_urls': 18,
                'requests_attempted': 18,
                'analysis_failures': 0,
                'source_execution': source_counts,
            }},
            'reference_execution': {
                'successful_reference_coverage': 0.96,
                'non_get_discovered_targets': 1,
                'non_get_reference_targets': 1,
            },
        }), encoding='utf-8')

        result = summarize(
            output, [SimpleNamespace(site_id='sample')], 1,
            base_concurrency=4, base_rate_limit=2, base_seconds_limit=180,
            base_max_pages_per_host=300,
        )

        self.assertEqual(result['reference_coverage_passed_sites'], 1)
        self.assertEqual(result['crawl_complete_sites'], 0)
        self.assertEqual(result['strict_passed_sites'], 0)
        report = (output / 'summary.md').read_text(encoding='utf-8')
        self.assertIn('GET >=95%', report)
        self.assertIn('Crawl complete', report)
        self.assertIn('Basecrawler source-level queue yield', report)
        self.assertIn('| sample | 1 | a | 20 | 18 | 20 | 18 | 18 | 18 | 10 | 10 | 9 | 9 | timeout=2 |', report)


if __name__=='__main__':unittest.main()
