"""Persist collective prefill/decode fields on saved benchmark rows."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wattever.collective_metrics import enrich_run


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", help="Run JSON files; default results/runs/*.json")
    parser.add_argument("--dry-run", action="store_true", help="Report changes without writing")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1] / "results" / "runs"
    paths = [Path(p) for p in args.paths] if args.paths else sorted(root.glob("*.json"))
    changed_files = 0
    for path in paths:
        run = json.loads(path.read_text(encoding="utf-8"))
        before = json.dumps(run.get("results") or [], sort_keys=True)
        count = enrich_run(run)
        after = json.dumps(run.get("results") or [], sort_keys=True)
        if before == after:
            print(f"{path.name}: unchanged ({count} rows touched)")
            continue
        changed_files += 1
        print(f"{path.name}: updated {count} result row(s)")
        if not args.dry_run:
            path.write_text(json.dumps(run, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0 if changed_files or args.dry_run else 0


if __name__ == "__main__":
    raise SystemExit(main())
