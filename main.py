#!/usr/bin/env python3
"""Desktop-first application entrypoint for BladeEye."""

from __future__ import annotations

import argparse

from bladeeye_pro import run_desktop_app


SAFE_CENTER_FREQ_HZ = 433_920_000.0
SAFE_SAMPLE_RATE_SPS = 1_000_000.0
SAFE_GAIN_DB = 20.0


def parse_args() -> argparse.Namespace:
    """Parse startup options."""
    parser = argparse.ArgumentParser(
        description="Run BladeEye in native desktop mode"
    )
    parser.add_argument(
        "--desktop-pro",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Launch the BladeEye Pro desktop runtime.",
    )
    parser.add_argument(
        "--center-freq",
        type=float,
        default=SAFE_CENTER_FREQ_HZ,
        help="Center frequency in Hz.",
    )
    parser.add_argument(
        "--sample-rate",
        type=float,
        default=SAFE_SAMPLE_RATE_SPS,
        help="Sample rate in samples per second.",
    )
    parser.add_argument(
        "--gain",
        type=float,
        default=SAFE_GAIN_DB,
        help="RF gain in dB.",
    )
    return parser.parse_args()


def main() -> None:
    """Run the desktop app with safe defaults when no CLI flags are provided."""
    args = parse_args()

    if not args.desktop_pro:
        raise SystemExit(
            "Error: web mode was removed. Use '--desktop-pro' (default) to start BladeEye desktop."
        )

    argv = [
        f"--center-freq={args.center_freq}",
        f"--sample-rate={args.sample_rate}",
        f"--gain={args.gain}",
    ]
    raise SystemExit(run_desktop_app(argv))


if __name__ == "__main__":
    main()
