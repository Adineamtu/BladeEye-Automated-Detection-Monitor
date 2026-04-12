import { useEffect, useState } from 'react';

function SessionManager({ onLoad, currentSignals, watchlist }) {
  const [sessions, setSessions] = useState([]);
  const [selected, setSelected] = useState('');

  useEffect(() => {
    async function fetchSessions() {
      try {
        const res = await fetch('/api/sessions');
        if (!res.ok) throw new Error('Failed to list sessions');
        setSessions(await res.json());
      } catch (err) {
        console.error(err);
      }
    }
    fetchSessions();
  }, []);

  useEffect(() => {
    async function recover() {
      try {
        const res = await fetch('/api/session/recover');
        if (!res.ok) return;
        const data = await res.json();
        if (data && (data.signals?.length || data.watchlist?.length || data.recordings?.length)) {
          if (window.confirm('Restore previous session?')) {
            onLoad(data);
          }
        }
      } catch (err) {
        console.error(err);
      }
    }
    recover();
  }, [onLoad]);

  async function load() {
    if (!selected) return;
    try {
      const res = await fetch(`/api/sessions/${encodeURIComponent(selected)}`);
      if (!res.ok) throw new Error('Failed to load session');
      const data = await res.json();
      onLoad(data);
    } catch (err) {
      console.error(err);
    }
  }

  async function save() {
    const name = prompt('Session name');
    if (!name) return;
    try {
      const res = await fetch(`/api/sessions/${encodeURIComponent(name)}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ signals: currentSignals, watchlist }),
      });
      if (!res.ok) throw new Error('Failed to save session');
      const listRes = await fetch('/api/sessions');
      setSessions(await listRes.json());
      setSelected(`${name}${name.endsWith('.json') ? '' : '.json'}`);
    } catch (err) {
      console.error(err);
    }
  }

  async function downloadReport() {
    if (!selected) return;
    try {
      const res = await fetch(`/api/sessions/${encodeURIComponent(selected)}/report`);
      if (!res.ok) throw new Error('Failed to generate report');
      const blob = await res.blob();
      const url = window.URL.createObjectURL(blob);
      const base = selected.endsWith('.json') ? selected.slice(0, -5) : selected;
      const link = document.createElement('a');
      link.href = url;
      link.download = `${base}_report.html`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(url);
    } catch (err) {
      console.error(err);
    }
  }

  async function downloadPdf() {
    if (!selected) return;
    try {
      const res = await fetch(`/api/sessions/${encodeURIComponent(selected)}/report.pdf`);
      if (!res.ok) throw new Error('Failed to export PDF');
      const blob = await res.blob();
      const url = window.URL.createObjectURL(blob);
      const base = selected.endsWith('.json') ? selected.slice(0, -5) : selected;
      const link = document.createElement('a');
      link.href = url;
      link.download = `${base}_report.pdf`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(url);
    } catch (err) {
      console.error(err);
    }
  }

  return (
    <div className="session-manager">
      <select value={selected} onChange={(e) => setSelected(e.target.value)}>
        <option value="">-- select session --</option>
        {sessions.map((s) => (
          <option key={s} value={s}>
            {s}
          </option>
        ))}
      </select>
      <button type="button" onClick={load} disabled={!selected}>
        Load Session
      </button>
      <button type="button" onClick={save}>
        Save Session
      </button>
      <button type="button" onClick={downloadReport} disabled={!selected}>
        Download Report
      </button>
      <button type="button" onClick={downloadPdf} disabled={!selected}>
        Export as PDF
      </button>
    </div>
  );
}

export default SessionManager;

