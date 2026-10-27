"""Refresh the expanded documentation index, or check freshness and local links."""
import argparse
from pathlib import Path
import posixpath
import re


ROOT = Path(__file__).resolve().parents[1]
DOCUMENTS = ["README.md", "docs/README.md", "docs/getting-started.md", "docs/browser.md", "docs/measurements.md",
             "docs/architecture.md", "docs/api.md", "docs/faq.md", "docs/troubleshooting.md",
             "CONTRIBUTING.md", "SECURITY.md", "CODE_OF_CONDUCT.md", "CHANGELOG.md",
             "ACKNOWLEDGEMENTS.md", "LICENSE", "results/README.md"]
LINK = re.compile(r"(!?\[[^\]\n]*\]\()([^\s)]+)(\))")


def local_target(target):
    return not re.match(r"[a-zA-Z][a-zA-Z0-9+.-]*:", target) and not target.startswith("#")


def expanded():
    parts = [ROOT.joinpath("llms.txt").read_text(encoding="utf-8").strip()]
    for name in DOCUMENTS:
        text = ROOT.joinpath(name).read_text(encoding="utf-8").strip()
        def rebase(match):
            target = match[2]
            if local_target(target):
                target = posixpath.normpath(posixpath.join(posixpath.dirname(name), target))
            return match[1] + target + match[3]
        text = LINK.sub(rebase, text)
        parts.append("## %s\n\nCanonical source: [%s](%s)\n\n%s" % (name, name, name, text))
    return "\n\n---\n\n".join(parts) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    target = ROOT / "llms-full.txt"
    expected = expanded()
    if args.check:
        errors = []
        if target.read_text(encoding="utf-8") != expected:
            errors.append("llms-full.txt is stale; run python tools/update_docs.py")
        for name in DOCUMENTS + ["llms.txt", "llms-full.txt", "skills/wattever/SKILL.md"]:
            text = ROOT.joinpath(name).read_text(encoding="utf-8")
            for match in LINK.finditer(text):
                path = match[2].split("#", 1)[0]
                if path and local_target(path) and not (ROOT / Path(name).parent / path).exists():
                    errors.append("%s: missing link %s" % (name, path))
        if errors:
            raise SystemExit("\n".join(errors))
        print("Documentation index and local links are current")
    else:
        target.write_text(expected, encoding="utf-8")


if __name__ == "__main__":
    main()
