"""``install_python_package``: user-approved package installation via uv."""

import asyncio
import logging
import sys
import uuid

from ..frozen import is_frozen
from ..tools import ToolContext, ToolResult
from .constants import INSTALL_APPROVAL_TIMEOUT, INSTALL_EXEC_TIMEOUT, PACKAGE_NAME_RE

logger = logging.getLogger(__name__)


async def _install_package_handler(
    arguments: dict,
    context: ToolContext,
    app,
) -> ToolResult:
    """Handle package installation requests with user approval.

    Args:
        arguments: Tool arguments with 'package_name' and 'reason'.
        context: Tool execution context.
        app: ConsensusApp instance for event emission and user interaction.

    Returns:
        ToolResult indicating success or failure of installation.
    """
    if is_frozen():
        return ToolResult(
            content=(
                "Package installation is not available in the bundled "
                "desktop app. Install Consensus from PyPI instead "
                "(`uv tool install consensus-app`) to use this feature."
            ),
            is_error=True,
            metadata={"status": "unavailable_frozen"},
        )

    package_name = arguments.get("package_name", "").strip()
    reason = arguments.get("reason", "").strip()

    if not package_name:
        return ToolResult(content="No package name provided.", is_error=True)

    # Validate package name to prevent command injection
    if not PACKAGE_NAME_RE.match(package_name):
        return ToolResult(
            content=f"Invalid package name: '{package_name}'. "
            "Package names may only contain letters, numbers, dots, hyphens, and underscores.",
            is_error=True,
        )

    # Check if already installed.  Query the distribution (PyPI) name via
    # importlib.metadata rather than guessing the import name, which differs
    # for many packages (pillow→PIL, scikit-learn→sklearn, opencv-python→cv2).
    # package_name is already validated to bare [A-Za-z0-9._-], so embedding
    # it as a repr literal is safe.
    try:
        check_proc = await asyncio.create_subprocess_exec(
            sys.executable, "-c",
            f"import importlib.metadata as m; m.version({package_name!r})",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await check_proc.wait()
        if check_proc.returncode == 0:
            return ToolResult(
                content=f"Package '{package_name}' is already installed.",
                metadata={"package": package_name, "status": "already_installed"},
            )
    except Exception as e:
        # Not fatal: the user is still asked, and uv reports an existing install.
        logger.warning("Could not check whether '%s' is installed: %s", package_name, e)

    # Request user approval via the ask_user event pattern
    request_id = f"pkg_{context.discussion_id}_{uuid.uuid4().hex[:8]}"
    loop = asyncio.get_running_loop()
    future = loop.create_future()

    entity_name = ""
    if app.db:
        entity = app.db.get_entity(context.caller_entity_id)
        if entity:
            entity_name = entity.get("name", "")

    question = (
        f"**{entity_name or 'A participant'}** is requesting to install "
        f"Python package **{package_name}**.\n\n"
        f"**Reason:** {reason}\n\n"
        f"This will run: `uv pip install --python {sys.executable} {package_name}`\n\n"
        f"Type **yes** to approve or **no** to deny."
    )

    request_data = {
        "request_id": request_id,
        "discussion_id": context.discussion_id,
        "entity_id": context.caller_entity_id,
        "entity_name": entity_name,
        "question": question,
        "context": f"Package installation request: {package_name}",
    }

    app._pending_user_inputs[request_id] = (future, request_data)
    app.emit("user_input_request", request_data)

    logger.info("install_python_package: %s requests '%s' (request_id=%s)",
                entity_name, package_name, request_id)

    try:
        user_answer = await asyncio.wait_for(future, timeout=INSTALL_APPROVAL_TIMEOUT)
    except asyncio.TimeoutError:
        logger.warning("Package install approval timed out (request_id=%s)", request_id)
        return ToolResult(
            content="The user did not respond to the installation request in time.",
            is_error=True,
        )
    except asyncio.CancelledError:
        return ToolResult(content="Installation request was cancelled.", is_error=True)
    finally:
        app._pending_user_inputs.pop(request_id, None)

    # Check approval
    answer = user_answer.strip().lower()
    if answer not in ("yes", "y", "approve", "ok"):
        return ToolResult(
            content=f"The user denied installation of '{package_name}'.",
            is_error=True,
            metadata={"package": package_name, "status": "denied"},
        )

    # Run uv pip install into the interpreter the sandbox worker and the
    # already-installed check use — without --python, uv picks whatever venv
    # VIRTUAL_ENV or the working directory resolves to.
    logger.info("Installing package '%s' (approved by user)", package_name)
    try:
        install_proc = await asyncio.create_subprocess_exec(
            "uv", "pip", "install", "--python", sys.executable, package_name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            install_proc.communicate(),
            timeout=INSTALL_EXEC_TIMEOUT,
        )
    except asyncio.TimeoutError:
        install_proc.kill()
        await install_proc.wait()
        logger.warning("uv pip install '%s' timed out", package_name)
        return ToolResult(
            content=f"Installation of '{package_name}' timed out after "
            f"{INSTALL_EXEC_TIMEOUT:.0f} seconds.",
            is_error=True,
        )
    except FileNotFoundError:
        return ToolResult(
            content="'uv' is not available. Cannot install packages.",
            is_error=True,
        )
    except Exception as e:
        logger.error("Failed to install package '%s': %s", package_name, e)
        return ToolResult(
            content=f"Installation failed: {e}",
            is_error=True,
        )

    stdout_str = stdout_bytes.decode("utf-8", errors="replace")
    stderr_str = stderr_bytes.decode("utf-8", errors="replace")

    if install_proc.returncode != 0:
        error_output = stderr_str.strip() or stdout_str.strip()
        logger.warning("uv pip install '%s' failed: %s", package_name, error_output)
        return ToolResult(
            content=f"Installation of '{package_name}' failed:\n{error_output[:2000]}",
            is_error=True,
            metadata={"package": package_name, "status": "failed"},
        )

    logger.info("Successfully installed '%s'", package_name)
    return ToolResult(
        content=f"Successfully installed '{package_name}'. You can now use it with execute_python.",
        metadata={"package": package_name, "status": "installed"},
    )
