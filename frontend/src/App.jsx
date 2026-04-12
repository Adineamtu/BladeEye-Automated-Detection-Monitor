import { useEffect, useState } from 'react';
import DetectedSignalsPanel from './DetectedSignalsPanel';
import WaterfallPlot from './WaterfallPlot';
import WatchlistPanel from './WatchlistPanel';
import SessionManager from './SessionManager';
import SignalDetailPanel from './SignalDetailPanel';
import ControlPanel from './ControlPanel';
import PatternsPanel from './PatternsPanel';
import ProtocolManager from './ProtocolManager';
import './App.css';

function App() {
  const [signals, setSignals] = useState([]);
  const [watchlist, setWatchlist] = useState([]);
  const [selectedSignal, setSelectedSignal] = useState(null);
  const [activeTab, setActiveTab] = useState('signals');
  const [config, setConfig] = useState({
    center_freq: 0,
    samp_rate: 0,
    fft_size: 1024,
    gain: 0,
    alert_threshold: 0,
  });
  const [alert, setAlert] = useState(null);
  const [isScanning, setIsScanning] = useState(false);
  const [wsConnected, setWsConnected] = useState(false);
  const [wsBeat, setWsBeat] = useState(false);
  const [health, setHealth] = useState({
    healthy: false,
    buffer_fill_percent: 0,
    dropped_samples: 0,
  });

  useEffect(() => {
    async function fetchWatchlist() {
      try {
        const res = await fetch('/api/watchlist');
        if (!res.ok) throw new Error('Failed to fetch watchlist');
        setWatchlist(await res.json());
      } catch (err) {
        console.error('Failed to fetch watchlist', err);
      }
    }
    fetchWatchlist();
  }, []);

  useEffect(() => {
    let timer;
    async function fetchHealth() {
      try {
        const res = await fetch('/api/health');
        if (!res.ok) throw new Error('Health endpoint unavailable');
        const data = await res.json();
        setHealth({
          healthy: Boolean(data.healthy),
          buffer_fill_percent: Number(data.buffer_fill_percent ?? 0),
          dropped_samples: Number(data.dropped_samples ?? 0),
        });
      } catch (err) {
        console.debug('Health metrics unavailable', err);
        setHealth((prev) => ({ ...prev, healthy: false }));
      }
    }
    fetchHealth();
    timer = setInterval(fetchHealth, 1000);
    return () => clearInterval(timer);
  }, []);

  useEffect(() => {
    async function fetchConfig() {
      try {
        const res = await fetch('/api/config');
        if (!res.ok) throw new Error('Failed to fetch config');
        const data = await res.json();
        setConfig((prev) => ({ ...prev, ...data }));
      } catch (err) {
        console.error('Failed to fetch config', err);
      }
    }
    fetchConfig();
  }, []);

  useEffect(() => {
    const wsProtocol = window.location.protocol === 'https:' ? 'wss' : 'ws';
    const ws = new WebSocket(`${wsProtocol}://${window.location.host}/ws/alerts`);
    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        const freq = data.frequency?.toFixed?.(0);
        const power = data.peak_power?.toFixed?.(2);
        setAlert(`Alert: ${freq} Hz @ ${power} dBm`);
      } catch {
        setAlert(`Alert: ${event.data}`);
      }
    };
    return () => ws.close();
  }, []);

  useEffect(() => {
    let timer;
    async function fetchSignals() {
      try {
        const res = await fetch('/api/signals');
        if (!res.ok) throw new Error('Network response was not ok');
        setSignals(await res.json());
      } catch (err) {
        console.error('Failed to fetch signals', err);
      }
    }
    fetchSignals();
    timer = setInterval(fetchSignals, 5000);
    return () => clearInterval(timer);
  }, []);

  function handleLoad(data) {
    setSignals(data.signals || []);
    setWatchlist(data.watchlist || []);
  }

  async function addWatchlist(frequency) {
    try {
      await fetch('/api/watchlist', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ frequency }),
      });
      setWatchlist((prev) => (prev.includes(frequency) ? prev : [...prev, frequency]));
    } catch (err) {
      console.error('Failed to add to watchlist', err);
    }
  }

  async function removeWatchlist(frequency) {
    try {
      await fetch(`/api/watchlist/${frequency}`, { method: 'DELETE' });
      setWatchlist((prev) => prev.filter((f) => f !== frequency));
    } catch (err) {
      console.error('Failed to remove from watchlist', err);
    }
  }

  async function updateConfig(newCfg) {
    setConfig(newCfg);
    try {
      const previousRate = config.samp_rate;
      const nextRate = newCfg.samp_rate;
      const rateChanged = typeof nextRate === 'number' && nextRate !== previousRate;

      if (rateChanged) {
        const bandwidthRes = await fetch(`/api/config/bandwidth?value=${nextRate}`, {
          method: 'PUT',
        });
        if (!bandwidthRes.ok) {
          throw new Error('Failed to apply sample rate');
        }
        const confirmed = await bandwidthRes.json();
        setConfig((prev) => ({ ...prev, ...confirmed }));
      }

      const res = await fetch('/api/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(newCfg),
      });
      if (res.ok) {
        const confirmed = await res.json();
        setConfig((prev) => ({ ...prev, ...confirmed }));
      }
    } catch (err) {
      console.error('Failed to update config', err);
    }
  }

  function handleSpectrumFrame() {
    setWsBeat(true);
    setTimeout(() => setWsBeat(false), 140);
  }

  return (
    <div className="App">
      {alert && (
        <div className="alert-banner">
          <span>{alert}</span>
          <button type="button" onClick={() => setAlert(null)}>
            ×
          </button>
        </div>
      )}
      <nav className="main-nav">
        <button type="button" onClick={() => setActiveTab('signals')}>
          Signals
        </button>
        <button type="button" onClick={() => setActiveTab('patterns')}>
          Patterns
        </button>
        <button type="button" onClick={() => setActiveTab('protocols')}>
          Protocols
        </button>
        <button
          type="button"
          className="coming-soon"
          onClick={() => alert('Community features coming soon')}
        >
          Community
        </button>
      </nav>
      <div className="scan-status">Scan: {isScanning ? 'Running' : 'Stopped'}</div>
      <div className="system-health">
        <span className={`status-led ${health.healthy ? 'connected' : 'disconnected'}`} />
        SDR Core: {health.healthy ? 'Healthy' : 'Offline / Stale heartbeat'} · Buffer Load:{' '}
        {health.buffer_fill_percent.toFixed(1)}% · Dropped: {health.dropped_samples}
      </div>
      <div className="ws-heartbeat">
        <span
          className={`status-led ${wsConnected ? 'connected' : 'disconnected'} ${wsBeat ? 'pulse' : ''}`}
        />
        Spectrum WS: {wsConnected ? 'Connected' : 'Disconnected'}
      </div>
      {activeTab === 'patterns' ? (
        <PatternsPanel />
      ) : activeTab === 'protocols' ? (
        <ProtocolManager />
      ) : (
        <>
          <SessionManager onLoad={handleLoad} currentSignals={signals} watchlist={watchlist} />
          <ControlPanel
            config={config}
            onChange={updateConfig}
            isScanning={isScanning}
            setIsScanning={setIsScanning}
          />
          <WaterfallPlot
            watchlist={watchlist}
            signals={signals}
            onSelectSignal={setSelectedSignal}
            onSpectrumFrame={handleSpectrumFrame}
            onSocketStateChange={setWsConnected}
          />
          <WatchlistPanel watchlist={watchlist} onAdd={addWatchlist} onRemove={removeWatchlist} />
          <DetectedSignalsPanel
            signals={signals}
            onSelect={setSelectedSignal}
            alertThreshold={config.alert_threshold}
          />
          <SignalDetailPanel
            signal={selectedSignal}
            onClose={() => setSelectedSignal(null)}
            onAddWatchlist={addWatchlist}
            onRemoveWatchlist={removeWatchlist}
            watchlist={watchlist}
            config={config}
          />
        </>
      )}
      <footer className="app-footer">
        Free analysis-only version. Reactive jammer and community features coming soon.
      </footer>
    </div>
  );
}

export default App;
