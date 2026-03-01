"""Entry point for the consensus application."""

import argparse
import sys


def main():
    parser = argparse.ArgumentParser(
        description="Consensus - Moderated Discussion Platform"
    )
    parser.add_argument(
        "--web", action="store_true",
        help="Run as web server instead of desktop app",
    )
    parser.add_argument("--host", default="0.0.0.0", help="Web server host")
    parser.add_argument("--port", type=int, default=8080, help="Web server port")
    parser.add_argument("--debug", action="store_true", help="Enable debug mode")

    args = parser.parse_args()

    if args.web:
        try:
            from .server import launch_web
        except ImportError:
            print("Web mode requires aiohttp. Install with: pip install consensus[web]")
            sys.exit(1)
        import asyncio
        asyncio.run(launch_web(host=args.host, port=args.port))
    else:
        try:
            from .desktop import launch_desktop
        except ImportError:
            print("Desktop mode requires pywebview. Install with: pip install consensus[desktop]")
            sys.exit(1)
        launch_desktop(debug=args.debug)


if __name__ == "__main__":
    main()
