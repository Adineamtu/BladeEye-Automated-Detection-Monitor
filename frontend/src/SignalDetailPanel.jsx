import { useEffect, useState } from 'react';
import './SignalDetailPanel.css';
import ConstellationPlot from './ConstellationPlot';
import FrequencyDeviationPlot from './FrequencyDeviationPlot';
import downloadIQ from './downloadIQ';
import getSummaryText from './signalSummary';
import PowerTimeChart from './PowerTimeChart';
import FrequencyTimeChart from './FrequencyTimeChart';

function copyToClipboard(text) {
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text);
  } else {
    const textarea = document.createElement('textarea');
    textarea.value = text;
    document.body.appendChild(textarea);
    textarea.select();
    document.execCommand('copy');
    document.body.removeChild(textarea);
  }
}

export function parseIQBuffer(buf) {
  const arr = new Float32Array(buf);
  const out = [];
  for (let i = 0; i < arr.length; i += 2) {
    out.push([arr[i], arr[i + 1]]);
  }
  return out;
}

function Histogram({ hist }) {
  if (!hist.length) return <div className="chart-placeholder">No baud data</div>;
  const width = 260;
  const height = 100;
  const maxY = Math.max(...hist, 1);
  const barWidth = width / hist.length;
  return (
    <svg width={width} height={height} className="histogram">
      {hist.map((v, i) => {
        const h = (v / maxY) * height;
        return (
          <rect
            key={i}
            x={i * barWidth}
            y={height - h}
            width={barWidth - 2}
            height={h}
            fill="orange"
          />
        );
      })}
    </svg>
  );
}

function SignalDetailPanel({
  signal,
  onClose,
  onAddWatchlist,
  onRemoveWatchlist,
  watchlist,
  initialDecoded = null,
  config = {}
}) {
  const [baud, setBaud] = useState({ hist: [], bins: [] });
  const [iq, setIq] = useState([]);
  const [deviation, setDeviation] = useState({ times: [], deviations: [] });
  const [trace, setTrace] = useState({ times: [], frequencies: [] });
  const [showDecision, setShowDecision] = useState(false);
  const [isRecording, setIsRecording] = useState(false);
  const [countdown, setCountdown] = useState(0);
  const [recordComplete, setRecordComplete] = useState(false);
  const [decoded, setDecoded] = useState(initialDecoded);
  const [decodeTab, setDecodeTab] = useState('binary');
  const [learnBits, setLearnBits] = useState([]);

  useEffect(() => {
    if (!signal) return;
    async function fetchData() {
      try {
        const b = await fetch(`/api/signals/${signal.center_frequency}/baud`);
        if (b.ok) setBaud(await b.json());
        if (signal.modulation_type === 'FSK') {
          const d = await fetch(`/api/signals/${signal.center_frequency}/deviation`);
          if (d.ok) setDeviation(await d.json());
        } else {
          setDeviation({ times: [], deviations: [] });
        }
        if (signal.modulation_type === 'FSK' || config.hopping_enabled) {
          const t = await fetch(`/api/signals/${signal.center_frequency}/trace`);
          if (t.ok) setTrace(await t.json());
        } else {
          setTrace({ times: [], frequencies: [] });
        }
      } catch (err) {
        console.error('Failed to fetch signal details', err);
      }
    }
    fetchData();
  }, [signal, config]);

  useEffect(() => {
    if (!signal || signal.modulation_type !== 'PSK') {
      setIq([]);
      return;
    }
    async function fetchIQ() {
      try {
        const res = await fetch(`/api/signals/${signal.center_frequency}/iq`);
        if (res.ok) {
          const buf = await res.arrayBuffer();
          setIq(parseIQBuffer(buf));
        }
      } catch (err) {
        console.error('Failed to fetch I/Q data', err);
      }
    }
    fetchIQ();
  }, [signal]);

  const inWatchlist = signal && watchlist.includes(signal.center_frequency);

  async function handleWatchlist() {
    if (!signal) return;
    if (inWatchlist) {
      await onRemoveWatchlist(signal.center_frequency);
    } else {
      await onAddWatchlist(signal.center_frequency);
    }
  }

  function exportIQ() {
    if (!signal) return;
    downloadIQ(signal.center_frequency);
  }

  async function handleRecord() {
    if (!signal) return;
    if (isRecording) {
      await fetch(`/api/signals/${signal.center_frequency}/record`, { method: 'DELETE' });
      setIsRecording(false);
      setCountdown(0);
    } else {
      await fetch(`/api/signals/${signal.center_frequency}/record`, { method: 'POST' });
      setDecoded(null);
      setRecordComplete(false);
      setIsRecording(true);
      setCountdown(3);
    }
  }

  async function handleDecode() {
    if (!signal) return;
    try {
      const res = await fetch(`/api/signals/${signal.center_frequency}/decode`, { method: 'POST' });
      if (res.ok) {
        const data = await res.json();
        setDecoded(data);
        setDecodeTab('binary');
        if (data.binary) setLearnBits((arr) => [...arr, data.binary]);
      }
    } catch (err) {
      console.error('Failed to decode', err);
    }
  }

  async function handleLearnPattern() {
    if (learnBits.length < 2) return;
    const name = window.prompt('Pattern name?');
    if (!name) return;
    try {
      await fetch(`/api/patterns/${encodeURIComponent(name)}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ bitstrings: learnBits }),
      });
      setLearnBits([]);
    } catch (err) {
      console.error('Failed to learn pattern', err);
    }
  }

  useEffect(() => {
    if (!isRecording) return;
    if (countdown <= 0) {
      setIsRecording(false);
      setRecordComplete(true);
      return;
    }
    const t = setTimeout(() => setCountdown((c) => c - 1), 1000);
    return () => clearTimeout(t);
  }, [isRecording, countdown]);

  const summary = signal ? getSummaryText(signal, deviation, iq) : { text: '', icon: null };

  return (
    <div className={`detail-panel ${signal ? 'open' : ''}`}>
      {signal && (
        <>
          <button className="close-btn" onClick={onClose}>
            ×
          </button>
          <h3>{signal.center_frequency} Hz</h3>
          <p>Likely Purpose: {signal.likely_purpose || '-'}</p>
          <p>Label: {signal.label || '-'}</p>
          <p>Identified Protocol: {(decoded?.protocol?.name) || (signal.protocol?.name) || '-'}</p>
          <div className="summary-section">
            {summary.icon && (
              <img src={summary.icon} alt={`${signal.modulation_type} icon`} />
            )}
            <span>{summary.text}</span>
          </div>
          <div className="chart-section">
            <h4>Power vs Time</h4>
            <PowerTimeChart freq={signal.center_frequency} />
          </div>
          <div className="chart-section">
            <h4>Baud Rate Stability</h4>
            <Histogram hist={baud.hist} />
          </div>
          {signal.modulation_type === 'FSK' && (
            <div className="chart-section">
              <h4>Frequency Deviation</h4>
              <FrequencyDeviationPlot data={deviation} />
            </div>
          )}
          {(signal.modulation_type === 'FSK' || config.hopping_enabled) && (
            <div className="chart-section">
              <h4>Frequency vs Time</h4>
              <FrequencyTimeChart data={trace} />
            </div>
          )}
          {signal.modulation_type === 'PSK' && (
            <div className="chart-section">
              <h4>Constellation</h4>
              <ConstellationPlot
                samples={iq}
                showDecisionBoundaries={showDecision}
              />
              <label>
                <input
                  type="checkbox"
                  checked={showDecision}
                  onChange={() => setShowDecision((v) => !v)}
                />
                Show decision boundaries
              </label>
            </div>
          )}
          <div className="actions">
            <button onClick={handleWatchlist}>
              {inWatchlist ? 'Remove from Watchlist' : 'Add to Watchlist'}
            </button>
            <button onClick={exportIQ} title="Download raw 32-bit float I/Q data (.bin)">Download I/Q</button>
            <button onClick={handleRecord}>
              {isRecording ? `Stop${countdown > 0 ? ` (${countdown})` : ''}` : 'Record'}
            </button>
            {recordComplete && (
              <button onClick={handleDecode}>Decode</button>
            )}
            {learnBits.length > 1 && (
              <button onClick={handleLearnPattern}>Learn Pattern</button>
            )}
          </div>
          {decoded && (
            <div className="decode-section">
              <div className="tabs">
                {['binary', 'hex', 'ascii'].map((t) => (
                  <button
                    key={t}
                    className={decodeTab === t ? 'active' : ''}
                    onClick={() => setDecodeTab(t)}
                  >
                    {t.charAt(0).toUpperCase() + t.slice(1)}
                  </button>
                ))}
              </div>
              <pre className="decoded-output" data-testid="decoded-output">{decoded[decodeTab]}</pre>
              <div className="decode-actions">
                <button onClick={() => copyToClipboard(decoded[decodeTab])}>Copy</button>
                <button
                  onClick={() => {
                    const blob = new Blob([decoded[decodeTab]], { type: 'text/plain' });
                    const a = document.createElement('a');
                    a.href = URL.createObjectURL(blob);
                    a.download = `${signal.center_frequency}_${decodeTab}.txt`;
                    a.click();
                    URL.revokeObjectURL(a.href);
                  }}
                >
                  Export
                </button>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}

export default SignalDetailPanel;
