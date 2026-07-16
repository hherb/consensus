"""GUI entry point for the frozen Consensus.app bundle."""

from consensus.config import load_env
from consensus.desktop import launch_desktop


def main() -> None:
    load_env()
    launch_desktop()


if __name__ == "__main__":
    main()
