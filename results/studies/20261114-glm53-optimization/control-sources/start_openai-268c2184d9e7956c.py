"""Compatibility entrypoint: open Strata Control without starting a server."""
from pathlib import Path
import subprocess


def main():
    root = Path(__file__).resolve().parent
    subprocess.Popen([str(root / '.venv/Scripts/pythonw.exe'), str(root / 'strata_app.py')],
                     cwd=root, creationflags=subprocess.CREATE_NO_WINDOW)


if __name__ == '__main__':
    main()
