import { useState, useEffect } from 'react';

export const PRESETS = {
  quickScanEurope: {
    label: 'Quick Scan (Europe)',
    center_freq: 868000000,
    samp_rate: 2000000,
    fft_size: 2048,
    gain: 20,
  },
  wideband433: {
    label: 'Wideband 433 MHz',
    center_freq: 433920000,
    samp_rate: 10000000,
    fft_size: 4096,
    gain: 30,
  },
  fineTune: {
    label: 'Fine-tune Analysis',
    center_freq: 100000000,
    samp_rate: 1000000,
    fft_size: 8192,
    gain: 10,
  },
};

const SAMPLE_RATE_STEPS = [1_000_000, 2_000_000, 5_000_000, 10_000_000, 20_000_000];

function ControlPanel({ config, onChange, isScanning = false, setIsScanning }) {
  const [local, setLocal] = useState(config);
  const [preset, setPreset] = useState('');
  const [error, setError] = useState('');

  useEffect(() => {
    setLocal(config);
  }, [config]);

  function handle(field, value) {
    const updated = { ...local, [field]: value };
    setLocal(updated);
    onChange(updated);
  }

  function getNearestSampleRate(value) {
    return SAMPLE_RATE_STEPS.reduce((best, current) => (
      Math.abs(current - value) < Math.abs(best - value) ? current : best
    ), SAMPLE_RATE_STEPS[0]);
  }

  function applyPreset(key) {
    setPreset(key);
    if (PRESETS[key]) {
      const { center_freq, samp_rate, fft_size, gain } = PRESETS[key];
      const updated = { center_freq, samp_rate, fft_size, gain };
      setLocal(updated);
      onChange(updated);
    }
  }

  async function startScan() {
    setError('');
    try {
      const res = await fetch('/api/scan/start', { method: 'POST' });
      if (!res.ok) throw new Error('Failed to start');
      setIsScanning && setIsScanning(true);
    } catch (err) {
      setError('Failed to start scan');
    }
  }

  async function stopScan() {
    setError('');
    try {
      const res = await fetch('/api/scan/stop', { method: 'POST' });
      if (!res.ok) throw new Error('Failed to stop');
      setIsScanning && setIsScanning(false);
    } catch (err) {
      setError('Failed to stop scan');
    }
  }

  async function toggleHopping() {
    const enabled = !local.hopping_enabled;
    try {
      const res = await fetch('/api/hopping', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled }),
      });
      if (res.ok) {
        const cfg = await res.json();
        setLocal(cfg);
        onChange(cfg);
      } else {
        // Revert if request failed
        setLocal((prev) => ({ ...prev, hopping_enabled: !enabled }));
      }
    } catch (err) {
      console.error('Failed to toggle hopping', err);
    }
  }

  return (
    <div className="control-panel">
      <div className="scan-controls">
        <button type="button" onClick={startScan} disabled={isScanning}>
          Start
        </button>
        <button type="button" onClick={stopScan} disabled={!isScanning}>
          Stop
        </button>
      </div>
      {error && <div className="error" role="alert">{error}</div>}
      <label>
        Presets
        <select value={preset} onChange={(e) => applyPreset(e.target.value)}>
          <option value="">Select preset</option>
          {Object.entries(PRESETS).map(([key, p]) => (
            <option key={key} value={key}>
              {p.label}
            </option>
          ))}
        </select>
      </label>
      {preset === 'fineTune' && (
        <>
          <label>
            Center Frequency (Hz)
            <input
              type="number"
              value={local.center_freq ?? ''}
              onChange={(e) => {
                const v = parseFloat(e.target.value);
                if (!Number.isNaN(v)) handle('center_freq', v);
              }}
            />
          </label>
          <label>
            Sample Rate (Hz)
            <input
              type="number"
              value={local.samp_rate ?? ''}
              onChange={(e) => {
                const v = parseFloat(e.target.value);
                if (!Number.isNaN(v)) handle('samp_rate', v);
              }}
            />
          </label>
          <label>
            FFT Size
            <input
              type="number"
              value={local.fft_size ?? ''}
              onChange={(e) => {
                const v = parseInt(e.target.value, 10);
                if (!Number.isNaN(v)) handle('fft_size', v);
              }}
            />
          </label>
          <label>
            Gain (dB)
            <input
              type="number"
              value={local.gain ?? ''}
              onChange={(e) => {
                const v = parseFloat(e.target.value);
                if (!Number.isNaN(v)) handle('gain', v);
              }}
            />
          </label>
        </>
      )}
      <label>
        Sample Rate (MHz): {((local.samp_rate ?? SAMPLE_RATE_STEPS[1]) / 1_000_000).toFixed(0)}
        <input
          type="range"
          min="0"
          max={String(SAMPLE_RATE_STEPS.length - 1)}
          step="1"
          value={String(
            Math.max(
              0,
              SAMPLE_RATE_STEPS.indexOf(
                getNearestSampleRate(local.samp_rate ?? SAMPLE_RATE_STEPS[1]),
              ),
            ),
          )}
          onChange={(e) => {
            const idx = parseInt(e.target.value, 10);
            if (!Number.isNaN(idx) && SAMPLE_RATE_STEPS[idx] !== undefined) {
              handle('samp_rate', SAMPLE_RATE_STEPS[idx]);
            }
          }}
        />
      </label>
      <label>
        Gain (dB): {Math.round(local.gain ?? 0)}
        <input
          type="range"
          min="0"
          max="90"
          step="1"
          value={local.gain ?? 0}
          onChange={(e) => {
            const v = parseFloat(e.target.value);
            if (!Number.isNaN(v)) handle('gain', v);
          }}
        />
      </label>
      <label>
        Alert Threshold (dBm)
        <input
          type="number"
          value={local.alert_threshold ?? ''}
          onChange={(e) => {
            const v = parseFloat(e.target.value);
            if (!Number.isNaN(v)) handle('alert_threshold', v);
          }}
        />
      </label>
      <label>
        <input
          type="checkbox"
          checked={local.hopping_enabled ?? false}
          onChange={toggleHopping}
        />
        Enable Hopping
      </label>
      <div>Active Frequency: {local.current_freq ?? local.center_freq ?? 0}</div>
    </div>
  );
}

export default ControlPanel;
