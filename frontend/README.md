# React + Vite

This template provides a minimal setup to get React working in Vite with HMR and some ESLint rules.

Currently, two official plugins are available:

- [@vitejs/plugin-react](https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react) uses [Babel](https://babeljs.io/) for Fast Refresh
- [@vitejs/plugin-react-swc](https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react-swc) uses [SWC](https://swc.rs/) for Fast Refresh

## Expanding the ESLint configuration

If you are developing a production application, we recommend using TypeScript with type-aware lint rules enabled. Check out the [TS template](https://github.com/vitejs/vite/tree/main/packages/create-vite/template-react-ts) for information on how to integrate TypeScript and [`typescript-eslint`](https://typescript-eslint.io) in your project.

## Control Panel Presets

The control panel offers several presets for common analysis scenarios:

- **Quick Scan (Europe)** (`quickScanEurope`):
  - `center_freq`: `868000000`
  - `samp_rate`: `2000000`
  - `fft_size`: `2048`
  - `gain`: `20`
- **Wideband 433 MHz** (`wideband433`):
  - `center_freq`: `433920000`
  - `samp_rate`: `10000000`
  - `fft_size`: `4096`
  - `gain`: `30`
- **Fine-tune Analysis** (`fineTune`):
  - `center_freq`: `100000000`
  - `samp_rate`: `1000000`
  - `fft_size`: `8192`
  - `gain`: `10`
  - Selecting this preset reveals advanced controls for manual adjustment.

