from __future__ import annotations

import sys
from pathlib import Path

import nbformat
from nbclient import NotebookClient


ROOT = Path(__file__).resolve().parents[1]
NOTEBOOK_ROOT = ROOT / "research" / "notebooks"


def execute_notebook(path: Path) -> Path:
    # Keep executed output beside the source notebook for reproducibility.
    nb = nbformat.read(path, as_version=4)
    client = NotebookClient(nb, timeout=7200, kernel_name="python3")
    client.execute()
    out = path.with_name(path.stem + ".executed.ipynb")
    nbformat.write(nb, out)
    return out


def main() -> None:
    targets = [ROOT / arg for arg in sys.argv[1:]]
    if not targets:
        targets = [
            NOTEBOOK_ROOT / "01_datacorpus_eda_analysis.ipynb",
            NOTEBOOK_ROOT / "03_서울상권_공간OD_결합검증.ipynb",
        ]
    for target in targets:
        out = execute_notebook(target)
        print(f"Completed: {out}")


if __name__ == "__main__":
    main()
