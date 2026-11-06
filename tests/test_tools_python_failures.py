"""Characterization tests for the failure paths of ``consensus.tools_python``.

Written before the module was split into a package (issue #61). Handlers are
driven through ``create_python_provider(app).execute(...)`` — the entry point
production uses — and fakes sit only at the process edge
(``asyncio.create_subprocess_exec``), so these tests need no edits when the
code moves between submodules.
"""

import asyncio
import json
import logging
import os
import platform
import sys
import tempfile

import pytest

from consensus import sandbox_worker
from consensus.tools import ToolContext
from consensus.tools_python import create_python_provider
from consensus.tools_python.install import _install_package_handler
from .document_helpers import patch_where_defined


class FakeProc:
    """Stand-in for an ``asyncio.subprocess.Process``.

    Like the real thing, ``returncode`` is None until the process has exited
    (``communicate``/``wait`` returned, or ``kill`` was called).
    """

    def __init__(self, returncode: int = 0, stdout: bytes = b"",
                 stderr: bytes = b"", hang: bool = False) -> None:
        self.returncode: int | None = None
        self._final_returncode = returncode
        self._stdout = stdout
        self._stderr = stderr
        self._hang = hang
        self.killed = False
        self.waited = False

    async def communicate(self, input: bytes | None = None) -> tuple[bytes, bytes]:
        """Return the canned output, or block until cancelled when hanging."""
        if self._hang:
            await asyncio.sleep(3600)
        self.returncode = self._final_returncode
        return self._stdout, self._stderr

    async def wait(self) -> int:
        """Return the canned return code (blocking while hanging and not killed)."""
        if self._hang and not self.killed:
            await asyncio.sleep(3600)
        self.waited = True
        if self.returncode is None:
            self.returncode = self._final_returncode
        return self.returncode

    def kill(self) -> None:
        """Record that the process was killed."""
        self.killed = True
        self.returncode = -9


class FakeSpawner:
    """Replacement for ``asyncio.create_subprocess_exec`` that records argv.

    Each call pops the next item from *results*: a ``FakeProc`` is returned,
    an exception instance is raised.
    """

    def __init__(self, *results) -> None:
        self.results = list(results)
        self.calls: list[tuple] = []

    async def __call__(self, *argv, **kwargs):
        self.calls.append(argv)
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


class FakeApp:
    """Minimal ``ConsensusApp`` surface the install handler uses.

    ``emit`` answers the approval request immediately with *answer*;
    ``answer=None`` leaves it unanswered and ``cancel=True`` cancels it, the
    way ``_cancel_pending_user_inputs`` does on reset.
    """

    def __init__(self, answer: str | None = "yes", cancel: bool = False) -> None:
        self.db = None
        self._pending_user_inputs: dict = {}
        self.answer = answer
        self.cancel = cancel
        self.emitted: list[tuple[str, dict]] = []

    def emit(self, event: str, data: dict) -> None:
        """Record the event and resolve the pending future if configured."""
        self.emitted.append((event, data))
        future, _ = self._pending_user_inputs[data["request_id"]]
        if self.cancel:
            future.cancel()
        elif self.answer is not None:
            future.set_result(self.answer)


CTX = ToolContext(caller_entity_id=1, discussion_id=1)


async def _execute(arguments: dict) -> object:
    """Run execute_python through the provider."""
    return await create_python_provider().execute("execute_python", arguments, CTX)


async def _install(app: FakeApp, package: str = "some-pkg") -> object:
    """Run install_python_package through the provider."""
    provider = create_python_provider(app)
    return await provider.execute(
        "install_python_package", {"package_name": package, "reason": "testing"}, CTX,
    )


# --- provider registration ---


async def test_provider_without_app_has_only_execute():
    tools = await create_python_provider().list_tools()
    assert [t.name for t in tools] == ["execute_python"]


async def test_provider_with_app_adds_install():
    tools = await create_python_provider(FakeApp()).list_tools()
    assert [t.name for t in tools] == ["execute_python", "install_python_package"]


# --- wall-clock timeout ---


def test_wall_timeout_falls_back_and_logs_when_cpu_limit_fails(monkeypatch, caplog):
    def boom():
        raise OSError("no cpu count")

    monkeypatch.setattr(sandbox_worker, "_compute_cpu_time_limit", boom)
    from consensus.tools_python.constants import (
        FALLBACK_CPU_LIMIT_SECONDS, MIN_WALL_TIMEOUT_SECONDS, WALL_TIMEOUT_MULTIPLIER,
    )
    from consensus.tools_python.execution import _compute_wall_timeout
    with caplog.at_level(logging.WARNING, logger="consensus.tools_python"):
        timeout = _compute_wall_timeout()
    assert timeout == max(
        FALLBACK_CPU_LIMIT_SECONDS * WALL_TIMEOUT_MULTIPLIER, MIN_WALL_TIMEOUT_SECONDS,
    )
    assert any(r.levelno == logging.WARNING and "no cpu count" in r.getMessage()
               for r in caplog.records)


# --- execute_python ---


async def test_execute_without_sandbox_exec_runs_plain_subprocess(monkeypatch):
    """Off macOS the worker runs without the sandbox-exec wrapper."""
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    result = await _execute({"code": "print(6 * 7)"})
    assert not result.is_error
    assert "42" in result.content


async def test_execute_launch_failure_is_error(monkeypatch):
    monkeypatch.setattr(asyncio, "create_subprocess_exec",
                        FakeSpawner(OSError("exec format error")))
    result = await _execute({"code": "1"})
    assert result.is_error
    assert "Failed to start code execution environment" in result.content


@pytest.mark.parametrize("returncode,stderr,expected", [
    (-9, b"", "killed by signal 9"),
    (1, b"MemoryError", "exceeded the memory limit"),
    (-24, b"", "exceeded the CPU time limit"),
    (1, b"SIGXCPU", "exceeded the CPU time limit"),
    (3, b"worker blew up", "Execution error: worker blew up"),
    (3, b"", "Process exited with code 3"),
])
async def test_execute_crash_without_output(monkeypatch, returncode, stderr, expected):
    monkeypatch.setattr(asyncio, "create_subprocess_exec",
                        FakeSpawner(FakeProc(returncode=returncode, stderr=stderr)))
    result = await _execute({"code": "1"})
    assert result.is_error
    assert expected in result.content


async def test_execute_non_json_worker_output(monkeypatch, caplog):
    monkeypatch.setattr(asyncio, "create_subprocess_exec",
                        FakeSpawner(FakeProc(stdout=b"not json", stderr=b"trace")))
    with caplog.at_level(logging.WARNING, logger="consensus.tools_python"):
        result = await _execute({"code": "1"})
    assert result.is_error
    assert "unexpected worker output" in result.content
    assert "not json" in result.content, "the worker's stdout must not be dropped"
    assert "trace" in result.content
    assert any("non-JSON" in r.getMessage() for r in caplog.records)


async def test_execute_crash_is_logged(monkeypatch, caplog):
    monkeypatch.setattr(asyncio, "create_subprocess_exec",
                        FakeSpawner(FakeProc(returncode=3, stderr=b"profile rejected")))
    with caplog.at_level(logging.WARNING, logger="consensus.tools_python"):
        await _execute({"code": "1"})
    assert any(r.levelno == logging.WARNING and "profile rejected" in r.getMessage()
               for r in caplog.records)


def _darwin_with_sandbox_exec(monkeypatch) -> None:
    real_exists = os.path.exists
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    monkeypatch.setattr(os.path, "exists",
                        lambda p: p == "/usr/bin/sandbox-exec" or real_exists(p))


async def test_execute_wraps_worker_in_sandbox_exec_on_macos(monkeypatch):
    _darwin_with_sandbox_exec(monkeypatch)
    spawner = FakeSpawner(FakeProc(stdout=b'{"stdout": "x"}'))
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawner)
    await _execute({"code": "1"})
    assert spawner.calls[0][:2] == ("/usr/bin/sandbox-exec", "-p")
    assert "(deny network*)" in spawner.calls[0][2]


async def test_execute_runs_worker_directly_off_macos(monkeypatch):
    from consensus.frozen import worker_command

    monkeypatch.setattr(platform, "system", lambda: "Linux")
    spawner = FakeSpawner(FakeProc(stdout=b'{"stdout": "x"}'))
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawner)
    await _execute({"code": "1"})
    assert spawner.calls[0][0] == worker_command()[0]


async def test_execute_timeout_kills_worker(monkeypatch, caplog):
    from consensus.tools_python.execution import execute_python_handler

    worker = FakeProc(hang=True)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", FakeSpawner(worker))
    patch_where_defined(monkeypatch, execute_python_handler, "_compute_wall_timeout",
                        lambda: 0.01)
    with caplog.at_level(logging.WARNING, logger="consensus.tools_python"):
        result = await _execute({"code": "1"})
    assert result.is_error
    assert "timed out" in result.content
    assert worker.killed and worker.waited, "a timed-out worker must be killed and reaped"
    assert any("timed out" in r.getMessage() for r in caplog.records)


async def test_execute_cancellation_kills_worker(monkeypatch):
    worker = FakeProc(hang=True)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", FakeSpawner(worker))
    task = asyncio.create_task(_execute({"code": "1"}))
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert worker.killed and worker.waited, "a cancelled turn must not leave the worker running"


async def test_execute_removes_sandbox_dir(monkeypatch):
    made: list[str] = []
    real_mkdtemp = tempfile.mkdtemp

    def recording_mkdtemp(*args, **kwargs):
        path = real_mkdtemp(*args, **kwargs)
        made.append(path)
        return path

    monkeypatch.setattr(tempfile, "mkdtemp", recording_mkdtemp)
    monkeypatch.setattr(asyncio, "create_subprocess_exec",
                        FakeSpawner(FakeProc(stdout=b'{"stdout": "x"}')))
    await _execute({"code": "1"})
    assert made and not os.path.exists(made[0])


def _worker_json(**fields) -> FakeProc:
    return FakeProc(stdout=json.dumps(fields).encode())


async def test_format_shows_stderr_as_warnings(monkeypatch):
    monkeypatch.setattr(asyncio, "create_subprocess_exec",
                        FakeSpawner(_worker_json(stdout="hi", stderr="careful")))
    result = await _execute({"code": "1", "description": "demo"})
    assert not result.is_error
    assert result.content == "[demo]\n\nOutput:\nhi\n\nWarnings:\ncareful"
    assert result.metadata == {"description": "demo"}


async def test_format_hides_stderr_when_error(monkeypatch):
    monkeypatch.setattr(asyncio, "create_subprocess_exec",
                        FakeSpawner(_worker_json(error="Boom", stderr="noise")))
    result = await _execute({"code": "1"})
    assert result.is_error
    assert result.content == "Error:\nBoom"


async def test_format_empty_output(monkeypatch):
    monkeypatch.setattr(asyncio, "create_subprocess_exec", FakeSpawner(_worker_json()))
    result = await _execute({"code": "1"})
    assert result.content == "(no output)"
    assert result.metadata == {}


async def test_format_truncates_long_output(monkeypatch):
    from consensus.tools_python.constants import PYTHON_MAX_OUTPUT

    monkeypatch.setattr(asyncio, "create_subprocess_exec",
                        FakeSpawner(_worker_json(stdout="x" * (PYTHON_MAX_OUTPUT + 10))))
    result = await _execute({"code": "1"})
    assert result.content.endswith(f"[Output truncated at {PYTHON_MAX_OUTPUT} characters]")
    assert len(result.content) < PYTHON_MAX_OUTPUT + 100


# --- install_python_package ---



async def test_install_targets_running_interpreter(monkeypatch):
    """uv must install into the interpreter the worker runs on, not whatever
    venv the working directory resolves to."""
    spawner = FakeSpawner(FakeProc(returncode=1), FakeProc(returncode=0))
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawner)
    result = await _install(FakeApp("yes"))
    assert not result.is_error
    assert result.metadata == {"package": "some-pkg", "status": "installed"}
    assert spawner.calls[1] == ("uv", "pip", "install", "--python", sys.executable, "some-pkg")


async def test_install_check_failure_is_logged_and_still_asks(monkeypatch, caplog):
    spawner = FakeSpawner(OSError("no python"), FakeProc(returncode=0))
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawner)
    app = FakeApp("yes")
    with caplog.at_level(logging.WARNING, logger="consensus.tools_python"):
        result = await _install(app)
    assert not result.is_error
    assert app.emitted, "the user must still be asked"
    assert any(r.levelno == logging.WARNING and "no python" in r.getMessage()
               for r in caplog.records)


async def test_install_approval_timeout(monkeypatch):
    monkeypatch.setattr(asyncio, "create_subprocess_exec", FakeSpawner(FakeProc(returncode=1)))
    patch_where_defined(monkeypatch, _install_package_handler, "INSTALL_APPROVAL_TIMEOUT", 0.01)
    app = FakeApp(answer=None)
    result = await _install(app)
    assert result.is_error
    assert "did not respond" in result.content
    assert app._pending_user_inputs == {}


async def test_install_task_cancelled_during_approval_propagates(monkeypatch):
    """Cancelling the turn (e.g. an outer wait_for cap) must not be swallowed."""
    monkeypatch.setattr(asyncio, "create_subprocess_exec", FakeSpawner(FakeProc(returncode=1)))
    app = FakeApp(answer=None)
    task = asyncio.create_task(_install(app))
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert app._pending_user_inputs == {}


async def test_install_outer_wait_for_still_times_out(monkeypatch):
    monkeypatch.setattr(asyncio, "create_subprocess_exec", FakeSpawner(FakeProc(returncode=1)))
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(_install(FakeApp(answer=None)), timeout=0.01)


async def test_install_approval_cancelled(monkeypatch):
    monkeypatch.setattr(asyncio, "create_subprocess_exec", FakeSpawner(FakeProc(returncode=1)))
    app = FakeApp(cancel=True)
    result = await _install(app)
    assert result.is_error
    assert "cancelled" in result.content
    assert app._pending_user_inputs == {}


@pytest.mark.parametrize("answer", ["no", " N ", "deny"])
async def test_install_denial_words(monkeypatch, answer):
    spawner = FakeSpawner(FakeProc(returncode=1))
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawner)
    result = await _install(FakeApp(answer))
    assert result.is_error
    assert result.metadata["status"] == "denied"
    assert len(spawner.calls) == 1, "uv must not run on denial"


@pytest.mark.parametrize("answer", ["Yes.", "sure", "yes please"])
async def test_install_unrecognised_reply_is_not_reported_as_denial(monkeypatch, caplog, answer):
    spawner = FakeSpawner(FakeProc(returncode=1))
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawner)
    with caplog.at_level(logging.INFO, logger="consensus.tools_python"):
        result = await _install(FakeApp(answer))
    assert result.is_error
    assert result.metadata["status"] == "not_approved"
    assert "denied" not in result.content
    assert answer.strip() in result.content
    assert len(spawner.calls) == 1, "uv must not run without a clear approval"
    assert any(answer in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("answer", ["Yes", " y ", "approve", "OK"])
async def test_install_accepts_approval_words(monkeypatch, answer):
    monkeypatch.setattr(asyncio, "create_subprocess_exec",
                        FakeSpawner(FakeProc(returncode=1), FakeProc(returncode=0)))
    result = await _install(FakeApp(answer))
    assert not result.is_error


async def test_install_timeout_kills_uv(monkeypatch):
    uv = FakeProc(hang=True)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", FakeSpawner(FakeProc(returncode=1), uv))
    patch_where_defined(monkeypatch, _install_package_handler, "INSTALL_EXEC_TIMEOUT", 0.01)
    result = await _install(FakeApp("yes"))
    assert result.is_error
    assert "timed out" in result.content
    assert uv.killed and uv.waited, "a timed-out install must be killed and reaped"


async def test_install_cancellation_kills_uv(monkeypatch):
    uv = FakeProc(hang=True)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", FakeSpawner(FakeProc(returncode=1), uv))
    task = asyncio.create_task(_install(FakeApp("yes")))
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert uv.killed and uv.waited, "a cancelled install must not be left running"


async def test_install_check_timeout_kills_probe_and_still_asks(monkeypatch):
    probe = FakeProc(hang=True)
    monkeypatch.setattr(asyncio, "create_subprocess_exec",
                        FakeSpawner(probe, FakeProc(returncode=0)))
    patch_where_defined(monkeypatch, _install_package_handler, "INSTALL_CHECK_TIMEOUT", 0.01)
    app = FakeApp("yes")
    result = await _install(app)
    assert not result.is_error
    assert probe.killed and probe.waited
    assert app.emitted, "the user must still be asked"


async def test_install_uv_missing(monkeypatch, caplog):
    monkeypatch.setattr(asyncio, "create_subprocess_exec",
                        FakeSpawner(FakeProc(returncode=1), FileNotFoundError("uv")))
    with caplog.at_level(logging.WARNING, logger="consensus.tools_python"):
        result = await _install(FakeApp("yes"))
    assert result.is_error
    assert "'uv' is not available" in result.content
    assert "on PATH" in result.content
    assert any("not on PATH" in r.getMessage() for r in caplog.records)


async def test_install_unexpected_launch_error(monkeypatch):
    monkeypatch.setattr(asyncio, "create_subprocess_exec",
                        FakeSpawner(FakeProc(returncode=1), PermissionError("denied")))
    result = await _install(FakeApp("yes"))
    assert result.is_error
    assert "Installation failed: denied" in result.content


@pytest.mark.parametrize("stdout,stderr,expected", [
    (b"", b"No solution found", "No solution found"),
    (b"resolver said no", b"", "resolver said no"),
])
async def test_install_nonzero_exit(monkeypatch, stdout, stderr, expected):
    monkeypatch.setattr(asyncio, "create_subprocess_exec", FakeSpawner(
        FakeProc(returncode=1), FakeProc(returncode=2, stdout=stdout, stderr=stderr),
    ))
    result = await _install(FakeApp("yes"))
    assert result.is_error
    assert expected in result.content
    assert result.metadata == {"package": "some-pkg", "status": "failed"}


# --- app registration ---


def test_app_logs_a_warning_when_python_tools_fail_to_load(app, monkeypatch, caplog):
    """Python tools have no optional deps, so a load failure is a fault, not INFO."""
    def boom(app=None):
        raise ImportError("broken facade")

    monkeypatch.setattr("consensus.tools_python.create_python_provider", boom)
    with caplog.at_level(logging.WARNING, logger="consensus.app"):
        app._init_python_tools()
    assert any(r.levelno == logging.WARNING and "Python execution tools failed to load"
               in r.getMessage() and r.exc_info for r in caplog.records)
