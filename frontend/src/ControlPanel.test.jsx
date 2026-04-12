import { render, screen, fireEvent, cleanup, waitFor } from '@testing-library/react';
import { describe, test, expect, vi, afterEach } from 'vitest';
import '@testing-library/jest-dom/vitest';
import { useState } from 'react';
import ControlPanel, { PRESETS } from './ControlPanel';

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('ControlPanel presets', () => {
  test('selecting a preset updates config', () => {
    const onChange = vi.fn();
    render(
      <ControlPanel
        config={{}}
        onChange={onChange}
        isScanning={false}
        setIsScanning={() => {}}
      />,
    );
    const select = screen.getByLabelText(/presets/i);
    fireEvent.change(select, { target: { value: 'quickScanEurope' } });
    expect(onChange).toHaveBeenLastCalledWith({
      center_freq: PRESETS.quickScanEurope.center_freq,
      samp_rate: PRESETS.quickScanEurope.samp_rate,
      fft_size: PRESETS.quickScanEurope.fft_size,
      gain: PRESETS.quickScanEurope.gain,
    });
  });
});

describe('scan controls', () => {
  test('start and stop buttons toggle state', async () => {
    const Wrapper = () => {
      const [isScanning, setIsScanning] = useState(false);
      return (
        <ControlPanel
          config={{}}
          onChange={() => {}}
          isScanning={isScanning}
          setIsScanning={setIsScanning}
        />
      );
    };

    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve({}) });
    global.fetch = fetchMock;
    render(<Wrapper />);
    const startBtn = screen.getByRole('button', { name: /start/i });
    const stopBtn = screen.getByRole('button', { name: /stop/i });
    expect(startBtn).not.toBeDisabled();
    expect(stopBtn).toBeDisabled();

    fireEvent.click(startBtn);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/scan/start', { method: 'POST' }));
    expect(startBtn).toBeDisabled();
    expect(stopBtn).not.toBeDisabled();

    fireEvent.click(stopBtn);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/scan/stop', { method: 'POST' }));
    expect(startBtn).not.toBeDisabled();
    expect(stopBtn).toBeDisabled();
  });

  test('shows error on failure', async () => {
    const Wrapper = () => {
      const [isScanning, setIsScanning] = useState(false);
      return (
        <ControlPanel
          config={{}}
          onChange={() => {}}
          isScanning={isScanning}
          setIsScanning={setIsScanning}
        />
      );
    };

    const fetchMock = vi.fn().mockResolvedValue({ ok: false });
    global.fetch = fetchMock;
    render(<Wrapper />);
    const startBtn = screen.getByRole('button', { name: /start/i });
    fireEvent.click(startBtn);
    await waitFor(() => screen.getByRole('alert'));
    expect(screen.getByRole('alert')).toHaveTextContent(/failed to start scan/i);
  });
});

