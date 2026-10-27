"""Build a portable source release from committed files, excluding local results."""
import argparse
import ast
import hashlib
from pathlib import Path
import re
import subprocess


def build(root, ref, output_dir):
    root = Path(root).resolve()
    raw = subprocess.check_output(["git", "show", ref + ":wattever/__init__.py"],
                                  cwd=root, encoding="utf-8")
    version = next(ast.literal_eval(node.value) for node in ast.parse(raw).body
                   if isinstance(node, ast.Assign)
                   and any(isinstance(target, ast.Name) and target.id == "__version__"
                           for target in node.targets))
    if not isinstance(version, str) or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        raise ValueError("The committed version must be a semantic release version.")
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    asset = output_dir / ("Wattever-%s.zip" % version)
    subprocess.run(["git", "archive", "--format=zip", "--prefix=Wattever-%s/" % version,
                    "--output=" + str(asset), ref], cwd=root, check=True)
    digest = hashlib.sha256(asset.read_bytes()).hexdigest()
    checksum = output_dir / "SHA256SUMS.txt"
    checksum.write_text("%s  %s\n" % (digest, asset.name), encoding="utf-8")
    return asset, checksum


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ref", default="HEAD", help="Committed revision to package")
    parser.add_argument("--output-dir", default="dist")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    for path in build(root, args.ref, args.output_dir):
        print(path)


if __name__ == "__main__":
    main()
