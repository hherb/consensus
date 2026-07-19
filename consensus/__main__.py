"""Entry point for the consensus application."""

import argparse
import sys

from .config import load_env, setup_logging

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8080


def main() -> None:
    """Parse command-line arguments and launch desktop or web mode."""
    load_env()
    log_path = setup_logging()
    print(f"Logging to {log_path}")

    parser = argparse.ArgumentParser(
        description="Consensus - Moderated Discussion Platform"
    )
    parser.add_argument(
        "--web", action="store_true",
        help="Run as web server instead of desktop app",
    )
    parser.add_argument("--host", default=DEFAULT_HOST, help="Web server host")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Web server port")
    parser.add_argument("--debug", action="store_true", help="Enable debug mode")
    parser.add_argument(
        "--multi-user", action="store_true",
        help="Enable multi-user mode with per-session isolation (for public deployment)",
    )

    args = parser.parse_args()

    if args.web:
        try:
            from .server import launch_web
        except ImportError:
            print("Web mode requires aiohttp. Install with: uv pip install consensus-app")
            sys.exit(1)
        import asyncio
        asyncio.run(launch_web(
            host=args.host, port=args.port, multi_user=args.multi_user,
        ))
    else:
        try:
            from .desktop import launch_desktop
        except ImportError:
            print("Desktop mode requires pywebview. Install with: uv pip install consensus-app")
            sys.exit(1)
        launch_desktop(debug=args.debug)


if __name__ == "__main__":
    main()
