#!/usr/bin/env python3
"""Desktop-first application entrypoint for BladeEye."""

from __future__ import annotations

import argparse

from bladeeye_pro import run_desktop_app


def parse_args() -> argparse.Namespace:
    """Parse startup options."""
    parser = argparse.ArgumentParser(
        description="Run BladeEye in native desktop mode"
    )
    parser.add_argument(
        "--desktop-pro",
        action="store_true",
        help="Launch the BladeEye Pro desktop runtime.",
    )
    return parser.parse_args()


def main() -> None:
    """Run the desktop app or fail fast on invalid startup mode."""
    args = parse_args()

    if not args.desktop_pro:
        raise SystemExit(
            "Error: web mode was removed. Run with '--desktop-pro' to start BladeEye desktop."
        )

    raise SystemExit(run_desktop_app())


if __name__ == "__main__":
    main()
