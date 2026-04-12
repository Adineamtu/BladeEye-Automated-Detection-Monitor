import { useState } from 'react';
import './WatchlistPanel.css';

function WatchlistPanel({ watchlist, onAdd, onRemove }) {
  const [freq, setFreq] = useState('');

  async function handleSubmit(e) {
    e.preventDefault();
    const f = parseFloat(freq);
    if (Number.isNaN(f)) return;
    await onAdd(f);
    setFreq('');
  }

  return (
    <div className="watchlist-panel">
      <h2>Watchlist</h2>
      <form onSubmit={handleSubmit}>
        <input
          type="text"
          value={freq}
          onChange={(e) => setFreq(e.target.value)}
          placeholder="Frequency in Hz"
        />
        <button type="submit">Add</button>
      </form>
      <ul>
        {watchlist.map((f) => (
          <li key={f}>
            {f}
            <button type="button" onClick={() => onRemove(f)}>
              Remove
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

export default WatchlistPanel;
