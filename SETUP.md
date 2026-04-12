# Setup Guide

This guide explains how to install the required software for the passive monitoring script and how to verify that your SDR hardware is detected by the system.

## Required Packages

- **GNU Radio**: version 3.8 or later (tested with 3.8.1.0).
- **gr-osmosdr**: provides the `osmosdr` source and sink blocks.
  **BladeRF drivers** (`bladerf` packages).

## Installation

Below are example commands for common Linux distributions. These commands require root privileges (`sudo`).

### Debian/Ubuntu

```bash
sudo apt update
sudo apt install gnuradio gr-osmosdr bladerf libbladerf2 libbladerf-dev \
    python3-matplotlib
```

### Fedora

```bash
sudo dnf install gnuradio gr-osmosdr bladerf bladerf-devel \
    python3-matplotlib
```

### Arch Linux

```bash
sudo pacman -Syu gnuradio gr-osmosdr bladerf libbladerf \
    python-matplotlib
```

## Hardware Connection and Verification

1. Connect your BladeRF device via USB.
2. Verify the connection:
   - **BladeRF**:
     ```bash
     bladeRF-cli --probe
     ```
     This lists detected BladeRF devices.
3. If your user does not have permission to access the device, run the command with `sudo` or install the appropriate udev rules provided by the device packages.

Once the device is detected correctly you can proceed with the scripts in this repository.

## Configuration

All runtime configuration is handled within the application's ControlPanel.
