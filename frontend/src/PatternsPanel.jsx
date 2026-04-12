import { useEffect, useState } from 'react';
import './PatternsPanel.css';

function PatternsPanel() {
  const [patterns, setPatterns] = useState({});

  useEffect(() => {
    async function fetchPatterns() {
      try {
        const res = await fetch('/api/patterns');
        if (!res.ok) throw new Error('Failed to fetch patterns');
        setPatterns(await res.json());
      } catch (err) {
        console.error('Failed to fetch patterns', err);
      }
    }
    fetchPatterns();
  }, []);

  async function handleRename(name) {
    const newName = window.prompt('New name', name);
    if (!newName || newName === name) return;
    try {
      const res = await fetch(`/api/patterns/${name}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ new_name: newName }),
      });
      if (!res.ok) throw new Error('Failed to rename pattern');
      setPatterns((prev) => {
        const updated = { ...prev };
        updated[newName] = updated[name];
        delete updated[name];
        return updated;
      });
    } catch (err) {
      console.error('Failed to rename pattern', err);
    }
  }

  async function handleDelete(name) {
    if (!window.confirm(`Delete pattern ${name}?`)) return;
    try {
      const res = await fetch(`/api/patterns/${name}`, { method: 'DELETE' });
      if (!res.ok) throw new Error('Failed to delete pattern');
      setPatterns((prev) => {
        const updated = { ...prev };
        delete updated[name];
        return updated;
      });
    } catch (err) {
      console.error('Failed to delete pattern', err);
    }
  }

  return (
    <div className="patterns-panel">
      <h2>Patterns</h2>
      <ul>
        {Object.keys(patterns).map((name) => (
          <li key={name}>
            <span className="pattern-name">{name}</span>
            <button type="button" onClick={() => handleRename(name)}>
              Rename
            </button>
            <button type="button" onClick={() => handleDelete(name)}>
              Delete
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

export default PatternsPanel;
