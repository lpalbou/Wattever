"""Normalize the pinned MMLU-Pro test parquet for offline capability use.

Preparation-only dependency: duckdb==1.4.3. Runtime Wattever uses stdlib JSON.
Supply the published parquet explicitly; this command never downloads data.
"""
import argparse
import hashlib
import json
from pathlib import Path

REVISION = 'b189ec765aa7ed75c8acfea42df31fdae71f97be'
PARQUET_SHA256 = '0e24a191921c2f453518a537a8b2117bd137e7714d4ef1565e9ba06c1ecb9ad8'
ROOT = Path(__file__).resolve().parents[1]


def normalize(source):
    if hashlib.sha256(source.read_bytes()).hexdigest() != PARQUET_SHA256:
        raise ValueError('Source does not match the pinned official test parquet.')
    import duckdb
    connection = duckdb.connect(config={'threads': 1, 'memory_limit': '64MB'})
    try:
        rows = connection.execute(
            'SELECT question_id, question, options, answer, answer_index, category, src '
            'FROM read_parquet(?) ORDER BY question_id', [str(source)]).fetchall()
    finally:
        connection.close()
    if len(rows) != 12032 or len({row[0] for row in rows}) != 12032:
        raise ValueError('Unexpected test count or duplicate upstream question IDs.')
    result = []
    for question_id, question, options, answer, index, category, origin in rows:
        if (type(question_id) is not int or type(index) is not int
                or not isinstance(question, str) or not question
                or not isinstance(options, list) or not 2 <= len(options) <= 10
                or any(not isinstance(option, str) or not option for option in options)
                or not 0 <= index < len(options) or answer != chr(65 + index)
                or not isinstance(category, str) or not category):
            raise ValueError('Malformed official question/choices/answer metadata.')
        result.append(dict(question_id=question_id, question=question, options=options,
                           answer=answer, answer_index=index, category=category, src=origin))
    # Gold cot_content is deliberately excluded from the bundled rows and prompts.
    return b''.join((json.dumps(row, ensure_ascii=False, sort_keys=True,
                               separators=(',', ':')) + '\n').encode('utf-8') for row in result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--write', action='store_true', help='Write the canonical bundled JSONL.')
    args = parser.parse_args()
    data = normalize(args.source)
    if args.write:
        target = ROOT / 'corpora/capabilities/mmlu-pro-test.jsonl'
        target.write_bytes(data)
    print(json.dumps({'revision': REVISION, 'source_sha256': PARQUET_SHA256,
                      'normalized_sha256': hashlib.sha256(data).hexdigest(),
                      'normalized_bytes': len(data), 'items': 12032, 'written': args.write}))


if __name__ == '__main__':
    main()
