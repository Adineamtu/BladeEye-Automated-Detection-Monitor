import { useEffect, useMemo, useState } from 'react';

function downloadBlob(contentType, filename, content) {
  const blob = new Blob([content], { type: contentType });
  const url = window.URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.URL.revokeObjectURL(url);
}

export default function LiveIntelligenceLog() {
  const [items, setItems] = useState([]);
  const [watchlistOnly, setWatchlistOnly] = useState(false);
  const [frequencyFilter, setFrequencyFilter] = useState('');
  const [targets, setTargets] = useState([]);
  const [form, setForm] = useState({ label: '', center_frequency: '433900000', tolerance_hz: '25000', modulation_type: 'FSK' });
  const [hitAlert, setHitAlert] = useState('');

  async function loadTargets() {
    const res = await fetch('/api/sigint/targets');
    if (res.ok) setTargets(await res.json());
  }

  async function loadRows() {
    const query = new URLSearchParams({ limit: '250', watchlist_only: String(watchlistOnly) });
    if (frequencyFilter.trim()) query.set('frequency', frequencyFilter.trim());
    const res = await fetch(`/api/sigint/log?${query.toString()}`);
    if (!res.ok) return;
    const data = await res.json();
    setItems(data.items || []);
    const latestHit = (data.items || []).find((item) => item.watchlist_hit === 1);
    if (latestHit) {
      setHitAlert(`Watchlist hit @ ${(latestHit.center_frequency / 1e6).toFixed(4)} MHz (${latestHit.modulation_type || 'UNK'})`);
    }
  }

  useEffect(() => {
    loadTargets();
  }, []);

  useEffect(() => {
    loadRows();
    const timer = setInterval(loadRows, 2000);
    return () => clearInterval(timer);
  }, [watchlistOnly, frequencyFilter]);

  async function createTarget(e) {
    e.preventDefault();
    const payload = {
      label: form.label || 'Custom target',
      center_frequency: form.center_frequency ? Number(form.center_frequency) : null,
      tolerance_hz: Number(form.tolerance_hz || 25000),
      modulation_type: form.modulation_type || null,
    };
    const res = await fetch('/api/sigint/targets', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (res.ok) {
      setForm((prev) => ({ ...prev, label: '' }));
      await loadTargets();
    }
  }

  async function removeTarget(id) {
    await fetch(`/api/sigint/targets/${id}`, { method: 'DELETE' });
    await loadTargets();
  }

  async function exportLog(format) {
    const res = await fetch(`/api/sigint/export?format=${format}&watchlist_only=${watchlistOnly}`);
    if (!res.ok) return;
    const text = await res.text();
    if (format === 'csv') {
      downloadBlob('text/csv', 'sigint_log.csv', text);
    } else {
      downloadBlob('application/json', 'sigint_log.json', text);
    }
  }

  const rows = useMemo(() => items.slice(0, 150), [items]);

  return (
    <section className="runtime-logs-panel">
      <h3>Live Intelligence Log</h3>
      {hitAlert && <div className="alert-banner"><span>{hitAlert}</span></div>}
      <div style={{ display: 'flex', gap: '0.6rem', marginBottom: '0.8rem', flexWrap: 'wrap' }}>
        <button type="button" onClick={() => setWatchlistOnly((prev) => !prev)}>
          {watchlistOnly ? 'Show All' : 'Show Watchlist Only'}
        </button>
        <input
          type="number"
          placeholder="Filter by frequency (Hz)"
          value={frequencyFilter}
          onChange={(e) => setFrequencyFilter(e.target.value)}
        />
        <button type="button" onClick={() => exportLog('csv')}>Export CSV</button>
        <button type="button" onClick={() => exportLog('json')}>Export JSON</button>
      </div>

      <table style={{ width: '100%', fontSize: '0.86rem' }}>
        <thead>
          <tr>
            <th>Time (UTC)</th>
            <th>Frequency</th>
            <th>Protocol</th>
            <th>Message</th>
            <th>RSSI</th>
            <th>Hits</th>
            <th>Watch</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((item) => (
            <tr key={item.id}>
              <td>{new Date(item.last_seen_ts * 1000).toISOString()}</td>
              <td>{(item.center_frequency / 1e6).toFixed(4)} MHz</td>
              <td>{item.protocol_name || item.modulation_type || '-'}</td>
              <td>{item.decoded_payload || '-'}</td>
              <td>{Number(item.rssi_db || 0).toFixed(2)} dB</td>
              <td>{item.hit_count}</td>
              <td>{item.watchlist_hit ? '✅' : '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <h4 style={{ marginTop: '1rem' }}>Watch Targets</h4>
      <form onSubmit={createTarget} style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
        <input placeholder="Label" value={form.label} onChange={(e) => setForm((p) => ({ ...p, label: e.target.value }))} />
        <input type="number" placeholder="Center Hz" value={form.center_frequency} onChange={(e) => setForm((p) => ({ ...p, center_frequency: e.target.value }))} />
        <input type="number" placeholder="Tolerance Hz" value={form.tolerance_hz} onChange={(e) => setForm((p) => ({ ...p, tolerance_hz: e.target.value }))} />
        <input placeholder="Modulation" value={form.modulation_type} onChange={(e) => setForm((p) => ({ ...p, modulation_type: e.target.value }))} />
        <button type="submit">Add Target</button>
      </form>
      {targets.map((target) => (
        <div className="runtime-log-item" key={target.id}>
          {target.label} · {(Number(target.center_frequency || 0) / 1e6).toFixed(4)} MHz ± {target.tolerance_hz} Hz · {target.modulation_type || 'ANY'}
          <button type="button" onClick={() => removeTarget(target.id)} style={{ marginLeft: '0.6rem' }}>Remove</button>
        </div>
      ))}
    </section>
  );
}
