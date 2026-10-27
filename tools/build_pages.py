"""Build the browser-only site with relative assets for GitHub Pages subpaths."""
import argparse
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from wattever import __version__


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
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default="_site")
    print(build(parser.parse_args().output_dir))


if __name__ == "__main__":
    main()
