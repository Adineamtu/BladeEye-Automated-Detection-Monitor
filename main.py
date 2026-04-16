#!/usr/bin/env python3
"""Application entrypoint that wires the FastAPI backend to PassiveMonitor."""

import argparse

import uvicorn

import api


def parse_args() -> argparse.Namespace:
    """Parse startup options for monitor and API."""
    parser = argparse.ArgumentParser(
        description="Run Signal Detective API with an attached PassiveMonitor"
    )
    parser.add_argument("--host", default="127.0.0.1", help="API bind host")
    parser.add_argument("--port", type=int, default=8000, help="API bind port")
    parser.add_argument(
        "--center-freq",
        type=float,
        default=868e6,
        help="SDR center frequency in Hz",
    )
    parser.add_argument(
        "--samp-rate", type=float, default=2e6, help="SDR sample rate in Hz"
    )
    parser.add_argument(
        "--fft-size", type=int, default=1024, help="FFT size for spectrum endpoint"
    )
    parser.add_argument("--gain", type=float, default=30.0, help="Receiver gain in dB")
    parser.add_argument(
        "--device", type=str, default="bladerf=0", help="gr-osmosdr device string"
    )
    parser.add_argument(
        "--threshold", type=float, default=0.0, help="Power detection threshold"
    )
    parser.add_argument(
        "--detection-mode",
        type=str,
        default="ENERGY",
        help="Detection mode (FSK, ENERGY, ASK, PSK)",
    )
    parser.add_argument(
        "--desktop-pro",
        action="store_true",
        help="Launch the native BladeEye Pro desktop runtime instead of FastAPI.",
    )
    return parser.parse_args()


def main() -> None:
    """Register lazy monitor factory and run Uvicorn."""
    args = parse_args()

    if args.desktop_pro:
        from bladeeye_pro import run_desktop_app

        raise SystemExit(run_desktop_app())

    def build_monitor():
        # Delay heavy GNU Radio/bladeRF object creation until /api/scan/start.
        from backend.passive_monitor import PassiveMonitor

        return PassiveMonitor(
            center_freq=args.center_freq,
            samp_rate=args.samp_rate,
            bandwidth=args.samp_rate,
            fft_size=args.fft_size,
            rx_gain=args.gain,
            device=args.device,
            threshold=args.threshold,
            detection_mode=args.detection_mode,
        )

    api.monitor_factory = build_monitor
    api.config_state.update(
        {
            "center_freq": args.center_freq,
            "samp_rate": args.samp_rate,
            "fft_size": args.fft_size,
            "gain": args.gain,
        }
    )

    uvicorn.run(api.app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
