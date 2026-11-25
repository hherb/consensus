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
    writes = [line for line in profile.splitlines() if "file-write" in line]
    assert not any("/private/tmp" in line for line in writes)


def _run_in_profile(sandbox_dir: str, code: str) -> subprocess.CompletedProcess:
    """Run *code* with plain Python under the profile the handler builds."""
    cmd = _build_macos_sandbox_cmd(sys.executable, ["-I", "-c", code], sandbox_dir)
    assert cmd is not None
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


# --- reads (#163) ---


def test_read_paths_cover_the_interpreter_and_package_not_the_launch_dir(tmp_path, monkeypatch):
    import consensus
    from consensus.tools_python.execution import worker_read_paths

    def readable(path: str) -> bool:
        return any(os.path.realpath(path).startswith(p + os.sep) for p in paths)

    launch_dir = tmp_path / "launched-here"
    launch_dir.mkdir()
    monkeypatch.setattr(sys, "path", [str(launch_dir), *sys.path[1:], str(tmp_path / "gone")])
    paths = worker_read_paths()
    assert os.path.realpath(sys.base_prefix) in paths
    assert os.path.realpath(sys.prefix) in paths
    assert readable(consensus.__file__), "the worker must import consensus.sandbox_worker"
    assert os.path.realpath(launch_dir) not in paths, "sys.path[0] is the parent's own"
    assert os.path.realpath(tmp_path / "gone") not in paths, "missing entries are dropped"


def test_read_paths_include_editable_and_frozen_roots(tmp_path, monkeypatch):
    import types

    from consensus.tools_python import execution

    source = tmp_path / "src" / "pkg"
    source.mkdir(parents=True)
    finder = types.ModuleType("__editable___pkg_1_0_finder")
    finder.MAPPING = {"pkg": str(source)}
    monkeypatch.setitem(sys.modules, finder.__name__, finder)
    contents = tmp_path / "Consensus.app" / "Contents"
    (contents / "MacOS").mkdir(parents=True)
    monkeypatch.setattr(execution, "is_frozen", lambda: True)
    monkeypatch.setattr(sys, "executable", str(contents / "MacOS" / "Consensus"))
    paths = execution.worker_read_paths()
    assert os.path.realpath(source) in paths
    assert os.path.realpath(contents) in paths


def test_unquotable_path_is_skipped_and_logged(caplog):
    from consensus.tools_python.execution import _seatbelt_subpaths

    with caplog.at_level("WARNING", logger="consensus.tools_python"):
        rendered = _seatbelt_subpaths(['/ok', '/bad"path', "/bad\\path"])
    assert rendered == '(subpath "/ok")'
    assert sum("cannot quote" in r.getMessage() for r in caplog.records) == 2


def test_profile_denies_private_roots_and_re_allows_with_file_read_data(monkeypatch):
    """A later file-read* does not override a file-read-data deny in Seatbelt."""
    from consensus.tools_python.constants import SEATBELT_PRIVATE_ROOTS

    monkeypatch.setattr("consensus.tools_python.execution.platform.system", lambda: "Darwin")
    monkeypatch.setattr("consensus.tools_python.execution.os.path.exists", lambda p: True)
    monkeypatch.setattr("consensus.tools_python.execution.worker_read_paths", lambda: ["/py"])
    profile = _build_macos_sandbox_cmd("/usr/bin/python3", [], "/sb")[2]
    deny = next(line for line in profile.splitlines() if line.startswith("(deny file-read-data"))
    assert all(f'(subpath "{root}")' in deny for root in SEATBELT_PRIVATE_ROOTS)
    allow = next(line for line in profile.splitlines() if line.startswith("(allow file-read-data"))
    assert '(subpath "/py")' in allow
    assert f'(subpath "{os.path.realpath("/sb")}")' in allow
    assert profile.index(deny) < profile.index(allow), "the allow must come after the deny"


async def test_worker_starts_in_its_run_directory(monkeypatch):
    """The worker's sys.path[0] is its cwd, which must be readable under the profile."""
    spawner = FakeSpawner(FakeProc(stdout=b'{"stdout": "x"}'))
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawner)
    await create_python_provider().execute("execute_python", {"code": "1"}, CTX)
    assert spawner.kwargs[0]["cwd"] == spawner.kwargs[0]["env"]["TMPDIR"]


READ_PROBE = """
import os
try:
    os.listdir({path!r}) if os.path.isdir({path!r}) else open({path!r}).read()
    print("READ")
except PermissionError:
    print("DENIED")
"""


@needs_seatbelt
@pytest.mark.parametrize("where", ["private_tmp", "other_var_folders_dir"])
def test_seatbelt_denies_reads_outside_the_interpreter(sandbox_dir, tmp_path, where):
    """Files in /private/tmp or another /var/folders dir are not readable."""
    if where == "private_tmp":
        target = tempfile.mkdtemp(prefix="consensus-sb163-", dir="/private/tmp")
    else:
        target = str(tmp_path)  # pytest's own dir under /private/var/folders
    secret = os.path.join(target, "secret.txt")
    with open(secret, "w") as f:
        f.write("s3cret")
    try:
        for path in (secret, target):
            result = _run_in_profile(sandbox_dir, READ_PROBE.format(path=path))
            assert result.stdout.strip() == "DENIED", (path, result.stderr)
        own = _run_in_profile(sandbox_dir, READ_PROBE.format(path=sandbox_dir))
        assert own.stdout.strip() == "READ", own.stderr
    finally:
        if where == "private_tmp":
            shutil.rmtree(target, ignore_errors=True)
