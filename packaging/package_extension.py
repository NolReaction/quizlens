"""Build the standalone QuizLens browser extension archive."""

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from extension_package import extension_archive


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    output = parser.parse_args().output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(extension_archive(ROOT / "browser-extension"))
