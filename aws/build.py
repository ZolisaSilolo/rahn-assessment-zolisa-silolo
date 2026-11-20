"""Build the Lambda package into aws/.build/ (no Docker needed).

Downloads Linux (manylinux, CPython 3.12, x86_64) wheels for the runtime dependencies, then copies
src/, data/ and the handler. boto3 is not bundled because the Lambda Python runtime provides it.

    python aws/build.py
"""

import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "aws" / ".build"
RUNTIME_PACKAGES = ("google-genai", "pydantic", "python-dotenv")


def pinned(package: str) -> str:
    """Use the version pinned in requirements.txt so Lambda runs what the tests ran."""
    for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines():
        if re.match(rf"^{re.escape(package)}==", line.strip(), re.IGNORECASE):
            return line.strip()
    return package


def main() -> int:
    shutil.rmtree(BUILD, ignore_errors=True)
    BUILD.mkdir(parents=True)
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "--quiet", "--disable-pip-version-check",
         "--target", str(BUILD), "--platform", "manylinux2014_x86_64", "--implementation", "cp",
         "--python-version", "3.12", "--only-binary=:all:", *[pinned(p) for p in RUNTIME_PACKAGES]],
        check=True,
    )
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
    shutil.copytree(ROOT / "src", BUILD / "src", ignore=ignore)
    shutil.copytree(ROOT / "data", BUILD / "data", ignore=ignore)
    shutil.copy2(ROOT / "aws" / "handler.py", BUILD / "handler.py")
    size_mb = sum(f.stat().st_size for f in BUILD.rglob("*") if f.is_file()) / 1_048_576
    print(f"Built {BUILD.relative_to(ROOT)} ({size_mb:.1f} MB unzipped; Lambda zip limit is 250 MB unzipped)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
