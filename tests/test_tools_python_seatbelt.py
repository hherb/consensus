"""The worker's files and the macOS Seatbelt profile (#97, #163).

The handler makes a per-run directory, hands it to the worker as ``TMPDIR``
and ``cwd``, and names it, resolved, as the profile's only writable path.
Under the user-data roots the profile allows reads only of that directory and
the interpreter's own (``worker_read_paths``). The real-macOS tests run
``sandbox-exec`` with the profile the handler builds; elsewhere they are
skipped.
"""

import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from consensus.frozen import worker_command
from consensus.tools_python import create_python_provider
from consensus.tools_python.constants import SEATBELT_PRIVATE_ROOTS
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
@pytest.mark.parametrize("base", [
    "/private/tmp", "/private/var/tmp", str(Path.home()), tempfile.gettempdir(),
], ids=["private_tmp", "var_tmp", "home", "other_var_folders_dir"])
def test_seatbelt_denies_reads_outside_the_interpreter(sandbox_dir, base):
    """Files in home, the shared temp dirs or another /var/folders dir are not readable."""
    target = tempfile.mkdtemp(prefix="consensus-sb163-", dir=base)
    try:
        if (base == tempfile.gettempdir()
                and not os.path.realpath(target).startswith("/private/var/folders/")):
            pytest.skip(f"TMPDIR is not under /var/folders: {target}")
        secret = os.path.join(target, "secret.txt")
        with open(secret, "w") as f:
            f.write("s3cret")
        for path in (secret, target):
            result = _run_in_profile(sandbox_dir, READ_PROBE.format(path=path))
            assert result.stdout.strip() == "DENIED", (path, result.stderr)
        own = _run_in_profile(sandbox_dir, READ_PROBE.format(path=sandbox_dir))
        assert own.stdout.strip() == "READ", own.stderr
    finally:
        shutil.rmtree(target, ignore_errors=True)


def test_read_paths_drop_the_launch_dir_and_broad_entries(tmp_path, monkeypatch):
    """A path inserted before the launch dir, or one covering home, must not re-allow it."""
    from consensus.tools_python.execution import worker_read_paths

    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    inserted, narrow = tmp_path / "inserted", tmp_path / "narrow"
    inserted.mkdir()
    narrow.mkdir()
    monkeypatch.setattr(sys, "path", [
        str(inserted), str(cwd), "", str(Path.home()), "/", str(narrow), *sys.path[1:],
    ])
    paths = worker_read_paths()
    assert os.path.realpath(cwd) not in paths, "the launch dir moved off sys.path[0]"
    covered = [os.path.realpath(Path.home()), *SEATBELT_PRIVATE_ROOTS]
    for path in paths:
        assert not any(c == path or c.startswith(path.rstrip(os.sep) + os.sep)
                       for c in covered), path
    assert os.path.realpath(narrow) in paths, "a narrow entry is kept"


def test_unquotable_run_dir_is_refused(monkeypatch):
    monkeypatch.setattr("consensus.tools_python.execution.platform.system", lambda: "Darwin")
    monkeypatch.setattr("consensus.tools_python.execution.os.path.exists", lambda p: True)
    with pytest.raises(ValueError):
        _build_macos_sandbox_cmd("/usr/bin/python3", [], '/sb"x')


async def test_unquotable_run_dir_returns_an_error(monkeypatch):
    def refuse(*_args):
        raise ValueError("unquotable")

    monkeypatch.setattr("consensus.tools_python.execution._build_macos_sandbox_cmd", refuse)
    spawner = FakeSpawner(FakeProc(stdout=b'{"stdout": "x"}'))
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawner)
    result = await create_python_provider().execute("execute_python", {"code": "1"}, CTX)
    assert result.is_error
    assert not spawner.kwargs, "nothing runs without the sandbox it asked for"


async def test_profile_blocking_the_worker_gives_a_worded_error(monkeypatch, caplog):
    """A worker the profile stops from loading its files must not leak host paths."""
    stderr = (b"Traceback ...\nPermissionError: [Errno 1] Operation not permitted: "
              b"'/Users/someone/src/consensus/consensus/__init__.py'")
    monkeypatch.setattr("consensus.tools_python.execution._build_macos_sandbox_cmd",
                        lambda *a: ["/usr/bin/sandbox-exec", "-p", "x", *a[:1], *a[1]])
    spawner = FakeSpawner(FakeProc(returncode=1, stderr=stderr))
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawner)
    with caplog.at_level("ERROR", logger="consensus.tools_python"):
        result = await create_python_provider().execute("execute_python", {"code": "1"}, CTX)
    assert result.is_error
    assert "/Users/someone" not in result.content
    assert "not an error in your code" in result.content
    assert any("/Users/someone" in r.getMessage() for r in caplog.records), "the log keeps it"


@needs_seatbelt
async def test_real_worker_imports_a_compiled_package_under_seatbelt():
    pytest.importorskip("numpy")
    code = "import numpy\nprint(numpy.arange(3).sum())"
    result = await create_python_provider().execute("execute_python", {"code": code}, CTX)
    assert not result.is_error, result.content
    assert "3" in result.content


# --- remaining gaps (#166) ---


def test_read_paths_include_editable_namespace_roots(tmp_path, monkeypatch):
    """An editable finder's NAMESPACES maps a package to a list of dirs."""
    import types

    from consensus.tools_python.execution import worker_read_paths

    first, second = tmp_path / "ns_a", tmp_path / "ns_b"
    first.mkdir()
    second.mkdir()
    finder = types.ModuleType("__editable___nspkg_1_0_finder")
    finder.MAPPING = {}
    finder.NAMESPACES = {"nspkg": [str(first), str(second)]}
    monkeypatch.setitem(sys.modules, finder.__name__, finder)
    paths = worker_read_paths()
    assert os.path.realpath(first) in paths
    assert os.path.realpath(second) in paths


def test_pth_paths_mirror_site(tmp_path):
    """Path lines count, relative to the site dir; comments and import lines do not."""
    from consensus.tools_python.execution import pth_paths

    absolute = tmp_path / "elsewhere"
    absolute.mkdir()
    (tmp_path / "rel").mkdir()
    (tmp_path / "a.pth").write_text(
        f"# comment\n\n{absolute}\nrel\nimport sys; sys.path.append('/x')\n"
        "import\tthis\n")
    (tmp_path / "not-a-pth.txt").write_text(str(tmp_path / "nope"))
    assert sorted(pth_paths(str(tmp_path))) == sorted(
        [str(absolute), str(tmp_path / "rel")])
    assert pth_paths(str(tmp_path / "missing")) == []


def test_read_paths_include_pth_added_after_startup(tmp_path, monkeypatch):
    """A package installed at runtime may add a .pth the parent never processed."""
    import site

    from consensus.tools_python.execution import worker_read_paths

    site_dir, source = tmp_path / "site-packages", tmp_path / "src"
    site_dir.mkdir()
    source.mkdir()
    (site_dir / "late.pth").write_text(f"{source}\n")
    monkeypatch.setattr(site, "getsitepackages", lambda: [str(site_dir)])
    assert os.path.realpath(source) in worker_read_paths()


def test_worker_is_launched_by_path_without_a_path_zero():
    """The worker needs no installed consensus, and -P keeps its own dir off sys.path."""
    from consensus import frozen

    exe, args = frozen.worker_command()
    assert args[0] == "-P"
    assert Path(args[1]).name == "sandbox_worker.py" and Path(args[1]).is_file()


def test_read_paths_include_the_worker_script_dir(monkeypatch):
    """Uninstalled: no editable finder and no sys.path entry covers consensus/."""
    from consensus import sandbox_worker
    from consensus.tools_python.execution import worker_read_paths

    worker_dir = os.path.realpath(Path(sandbox_worker.__file__).parent)
    for name in [n for n in sys.modules if n.startswith("__editable__")]:
        monkeypatch.delitem(sys.modules, name)
    monkeypatch.setattr(sys, "path", [p for p in sys.path if not p or not (
        worker_dir + os.sep).startswith(os.path.realpath(p).rstrip(os.sep) + os.sep)])
    monkeypatch.setattr("site.getsitepackages", lambda: [])
    assert worker_dir in worker_read_paths()


def test_worker_runs_without_consensus_importable(tmp_path):
    """-S drops site-packages (and the editable finder): an uninstalled checkout."""
    exe, args = worker_command()
    result = subprocess.run([exe, "-S", *args], input="print(6 * 7)", cwd=tmp_path,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert "42" in json.loads(result.stdout)["stdout"]


def test_homebrew_data_dirs_are_private():
    assert {"/opt/homebrew/var", "/usr/local/var"} <= set(SEATBELT_PRIVATE_ROOTS)


@needs_seatbelt
@pytest.mark.skipif(not os.access("/opt/homebrew/var", os.W_OK),
                    reason="no writable /opt/homebrew/var")
def test_seatbelt_denies_reads_in_homebrew_var(sandbox_dir):
    target = tempfile.mkdtemp(prefix="consensus-sb166-", dir="/opt/homebrew/var")
    try:
        secret = os.path.join(target, "secret.txt")
        with open(secret, "w") as f:
            f.write("s3cret")
        result = _run_in_profile(sandbox_dir, READ_PROBE.format(path=secret))
        assert result.stdout.strip() == "DENIED", result.stderr
    finally:
        shutil.rmtree(target, ignore_errors=True)
