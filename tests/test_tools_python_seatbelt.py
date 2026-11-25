"""The worker's one writable directory and the macOS Seatbelt profile (#97).

The handler makes a per-run directory, hands it to the worker as ``TMPDIR``
and names it, resolved, as the profile's only writable path. The real-macOS
tests run ``sandbox-exec`` with the profile the handler builds; elsewhere
they are skipped.
"""

import asyncio
import os
import shutil
import subprocess
import sys
import tempfile

import pytest

from consensus.tools_python import create_python_provider
from consensus.tools_python.execution import _build_macos_sandbox_cmd

from .test_tools_python_failures import CTX, FakeProc, FakeSpawner

ON_MACOS_WITH_SEATBELT = sys.platform == "darwin" and os.path.exists("/usr/bin/sandbox-exec")
needs_seatbelt = pytest.mark.skipif(not ON_MACOS_WITH_SEATBELT,
                                    reason="needs macOS sandbox-exec")


async def test_worker_gets_the_sandbox_dir_as_tmpdir(monkeypatch):
    """The worker's scratch dir must land in the directory the profile allows."""
    spawner = FakeSpawner(FakeProc(stdout=b'{"stdout": "x"}'))
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawner)
    await create_python_provider().execute("execute_python", {"code": "1"}, CTX)
    env = spawner.kwargs[0]["env"]
    assert os.path.basename(env["TMPDIR"]).startswith("consensus-sandbox-")
    assert env["MPLCONFIGDIR"] == env["TMPDIR"]
    assert env["PATH"] == os.environ["PATH"], "the rest of the environment is kept"


def test_profile_names_the_resolved_dir_and_no_shared_tmp(tmp_path, monkeypatch):
    """Seatbelt matches resolved paths, and /private/tmp must not be writable."""
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    monkeypatch.setattr("consensus.tools_python.execution.platform.system", lambda: "Darwin")
    monkeypatch.setattr("consensus.tools_python.execution.os.path.exists", lambda p: True)
    profile = _build_macos_sandbox_cmd("/usr/bin/python3", [], str(link))[2]
    assert f'(subpath "{os.path.realpath(real)}")' in profile
    assert str(link) not in profile
    assert "/private/tmp" not in profile


def _run_in_profile(sandbox_dir: str, code: str) -> subprocess.CompletedProcess:
    """Run *code* with plain Python under the profile the handler builds."""
    cmd = _build_macos_sandbox_cmd(sys.executable, ["-I", "-c", code], sandbox_dir)
    return subprocess.run(cmd, capture_output=True, text=True, timeout=60,
                          env={**os.environ, "TMPDIR": sandbox_dir})


WRITE_PROBE = """
try:
    open({path!r}, "w").write("x")
    print("WROTE")
except PermissionError:
    print("DENIED")
"""


@pytest.fixture
def sandbox_dir():
    """A directory made the way the handler makes it (unresolved /var/folders/...)."""
    path = tempfile.mkdtemp(prefix="consensus-sandbox-")
    yield path
    shutil.rmtree(path, ignore_errors=True)


@needs_seatbelt
def test_seatbelt_allows_writes_in_the_sandbox_dir(sandbox_dir):
    inside = os.path.join(sandbox_dir, "ok.txt")
    result = _run_in_profile(sandbox_dir, WRITE_PROBE.format(path=inside))
    assert result.stdout.strip() == "WROTE", result.stderr
    temp = _run_in_profile(sandbox_dir, "import tempfile; print(tempfile.mkdtemp())")
    assert os.path.realpath(temp.stdout.strip()).startswith(os.path.realpath(sandbox_dir))


@needs_seatbelt
def test_seatbelt_denies_writes_to_shared_tmp(sandbox_dir):
    outside = f"/private/tmp/consensus-sb97-{os.getpid()}"
    try:
        result = _run_in_profile(sandbox_dir, WRITE_PROBE.format(path=outside))
        assert result.stdout.strip() == "DENIED", result.stderr
        assert not os.path.exists(outside)
    finally:
        if os.path.exists(outside):
            os.remove(outside)


@needs_seatbelt
async def test_real_worker_runs_under_seatbelt():
    """End to end: the real worker starts and writes its scratch files."""
    code = "open('f.txt', 'w').write('hi')\nprint(open('f.txt').read())"
    result = await create_python_provider().execute("execute_python", {"code": code}, CTX)
    assert not result.is_error, result.content
    assert "hi" in result.content
