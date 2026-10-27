"""Check benchmark contributions using only Python's standard library."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wattever.validation import validate_run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", help="JSON files; defaults to results/runs/*.json")
    args = parser.parse_args()
    paths = [Path(p) for p in args.paths] if args.paths else sorted(
        (Path(__file__).resolve().parents[1] / "results" / "runs").glob("*.json"))
    failed = False
    for path in paths:
        try:
            run = json.loads(path.read_text(encoding="utf-8"))
            errors = validate_run(run)
            if isinstance(run, dict) and run.get("id") != path.stem:
                errors.append("filename must match the benchmark id")
        except (OSError, ValueError) as exc:
            errors = [str(exc)]
        failed |= bool(errors)
        print("%s: %s" % (path.name, "; ".join(errors) if errors else "OK"))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
