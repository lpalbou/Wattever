"""Pinned external questions, answer mapping and a genuinely balanced subset."""
import collections
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from wattever import capabilities as cap, datasets


class MMLUDatasetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        datasets.register_bundled_suites()
        cls.full = cap.suite_manifest('mmlu-pro-test-chat-v1')
        cls.small = cap.suite_manifest('mmlu-pro-balanced64-chat-v1')

    def test_official_split_integrity_and_existing_protocols_are_unchanged(self):
        self.assertEqual(len(self.full['tasks']), 12032)
        self.assertEqual(len({t['id'] for t in self.full['tasks']}), 12032)
        self.assertEqual(len({t['category'] for t in self.full['tasks']}), 14)
        self.assertEqual(self.full['source']['revision'], datasets.MMLU_REVISION)
        self.assertEqual(self.full['source']['upstream_parquet_sha256'], datasets.MMLU_PARQUET_SHA256)
        self.assertEqual(self.full['digest'], '7943f64c2d586859804423b73b8403ca5e11731f0f3271d1dceabf143bbae574')
        self.assertEqual(cap.suite_manifest('arc-challenge-test-chat-v1')['digest'],
                         'e202216c2d80ba86c4d521d50f7742ce4c3641efb2f1225b1bc2995a1e78c75b')
        self.assertEqual(cap.suite_manifest('gsm8k-test-chat-v1')['digest'],
                         '6045d9e255a9c2369346d5746cd555db3d684d019105c0d58c627ea4a2880c3f')

    def test_balanced64_is_fixed_cross_subject_not_a_prefix(self):
        tasks = self.small['tasks']
        counts = collections.Counter(t['category'] for t in tasks)
        self.assertEqual(len(tasks), 64)
        self.assertEqual(len(counts), 14)
        self.assertEqual(set(counts.values()), {4, 5})
        self.assertEqual(self.small['digest'], 'f616c8650feab76dc3e9a7d08773c5c6876be418249c86724ea4053585bfeddf')
        self.assertEqual(self.small['source']['parent_suite_digest'], self.full['digest'])
        self.assertEqual(datasets._balanced_subset(list(reversed(self.full['tasks']))), tasks)
        self.assertNotEqual(datasets._balanced_subset(self.full['tasks'], seed=73130), tasks)
        self.assertEqual(set(cap.select_item_ids([t['id'] for t in tasks], 0, 73129)),
                         {t['id'] for t in tasks})

    def test_prompts_and_answer_keys_match_published_rows_without_gold_cot(self):
        rows = datasets._rows('mmlu-pro-test.jsonl', datasets.MMLU_SHA256, 12032)
        by_id = {row['question_id']: row for row in rows}
        for task in self.small['tasks']:
            row = by_id[int(task['id'].rsplit('-', 1)[1])]
            self.assertNotIn('cot_content', row)
            self.assertTrue(task['prompt'].startswith(row['question'] + '\n\n'))
            self.assertEqual(task['scorer']['expected'], row['answer'])
            self.assertEqual(row['answer'], chr(65 + row['answer_index']))
            self.assertEqual(len(task['scorer']['choices']), len(row['options']))
            for index, text in enumerate(row['options']):
                self.assertIn('%s. %s' % (chr(65 + index), text), task['prompt'])
            self.assertTrue(cap.score_response(task, 'Explanation.\nANSWER: ' + row['answer'])['correct'])
            self.assertFalse(cap.score_response(task, 'ANSWER: K')['correct'])

    def test_jsonl_unicode_separators_are_question_content_not_record_delimiters(self):
        raw = (json.dumps({'question': 'alpha\u2028beta\u0085gamma'}, ensure_ascii=False) + '\n').encode('utf-8')
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, 'sample.jsonl').write_bytes(raw)
            with patch.object(datasets, 'DATA', Path(folder)):
                rows = datasets._rows('sample.jsonl', hashlib.sha256(raw).hexdigest(), 1)
                self.assertEqual(rows, [{'question': 'alpha\u2028beta\u0085gamma'}])
                with self.assertRaisesRegex(ValueError, 'checksum'):
                    datasets._rows('sample.jsonl', '0' * 64, 1)

    def test_larger_valid_split_keeps_a_finite_import_bound(self):
        oversized = {k: v for k, v in self.small.items() if k != 'digest'}
        oversized['id'] = 'oversized-suite-test'
        oversized['tasks'] = [self.small['tasks'][0]] * (cap.MAX_SUITE_TASKS + 1)
        with self.assertRaisesRegex(ValueError, '20000'):
            cap.register_suite(oversized)


if __name__ == '__main__':
    unittest.main()
