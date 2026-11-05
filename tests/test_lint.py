"""Lint gate (issue #86): ``ruff --select F`` must stay clean on ``consensus/``.

There is no CI lint step, so this test is what stops unused names, undefined
names and dead assignments from accumulating again.
"""

import subprocess
import sys
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent.parent / "consensus"


def test_ruff_pyflakes_clean() -> None:
    """The package has zero pyflakes (F-class) findings."""
    result = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "--select", "F",
         "--output-format", "concise", str(PACKAGE_DIR)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
