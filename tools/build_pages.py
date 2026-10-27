"""Build the browser-only site with relative assets for GitHub Pages subpaths."""
import argparse
import json
from pathlib import Path
import shutil
import hashlib
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from wattever import __version__
from wattever.validation import validate_run


def build_catalog(output, root=ROOT):
    """Publish committed contributions, never untracked local measurements."""
    root = Path(root)
    names = subprocess.check_output(["git", "ls-tree", "-r", "--name-only", "HEAD", "--", "results/runs/"],
                                    cwd=root, encoding="utf-8").splitlines()
    records = {}
    for name in names:
        if not re.fullmatch(r"results/runs/[A-Za-z0-9][A-Za-z0-9_.-]{0,199}\.json", name):
            continue
        raw = subprocess.check_output(["git", "show", "HEAD:" + name], cwd=root)
        run = json.loads(raw.decode("utf-8"))
        errors = validate_run(run)
        if errors or run.get("id") != Path(name).stem:
            raise ValueError("Invalid shared benchmark %s: %s" % (name, errors))
        records[run["id"]] = (run, raw)
    folder = Path(output) / "benchmarks"
    folder.mkdir(parents=True, exist_ok=True)
    entries = []
    for run_id, (run, raw) in sorted(records.items(), key=lambda item: item[1][0].get("created_at", ""), reverse=True):
        filename = run_id + ".json"
        (folder / filename).write_bytes(raw)
        publication = run.get("publication")
        source = publication.get("source_run_id") if isinstance(publication, dict) else None
        if source and source in records:
            continue  # Keep its raw URL available without listing a superseded checkpoint twice.
        entries.append({"id": run_id, "file": filename, "sha256": hashlib.sha256(raw).hexdigest()})
    (folder / "index.json").write_text(json.dumps({"version": 1, "benchmarks": entries}, indent=2) + "\n", encoding="utf-8")
    return entries


def build(output_dir):
    output = Path(output_dir).resolve()
    assets = output / "static"
    assets.mkdir(parents=True, exist_ok=True)
    for source in (ROOT / "web").iterdir():
        if source.is_file() and source.suffix in (".js", ".css"):
            shutil.copyfile(source, assets / source.name)
    if not (assets / "browser.js").is_file():
        raise ValueError("The static browser backend is missing.")
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    html = html.replace('"/static/', '"./static/')
    boot = '<script>window.WATTEVER_STATIC=true;window.WATTEVER_VERSION=%s;</script>' % json.dumps(__version__)
    html = html.replace("</head>", boot + "\n</head>")
    (output / "index.html").write_text(html, encoding="utf-8")
    (output / ".nojekyll").write_text("", encoding="utf-8")
    shutil.copyfile(ROOT / "corpora" / "agentic-ai-oct2026.full.md", output / "corpus.md")
    build_catalog(output)
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default="_site")
    print(build(parser.parse_args().output_dir))


if __name__ == "__main__":
    main()
