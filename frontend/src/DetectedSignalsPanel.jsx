import './DetectedSignalsPanel.css';

function DetectedSignalsPanel({ signals, onSelect, alertThreshold }) {
  return (
    <div className="signals-panel">
      <h2>Detected Signals</h2>
      <table className="signals-table">
        <thead>
          <tr>
            <th>Center Frequency</th>
            <th>Modulation Type</th>
            <th>Baud Rate</th>
            <th>Likely Purpose</th>
            <th>Label</th>
            <th>Protocol</th>
            <th>Signal Strength</th>
            <th>Duration (s)</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          {signals.map((sig, idx) => (
            <tr
              key={idx}
              onClick={() => onSelect && onSelect(sig)}
              className={
                alertThreshold !== undefined && sig.peak_power > alertThreshold
                  ? 'alert-row'
                  : ''
              }
            >
              <td>{sig.center_frequency}</td>
              <td>{sig.modulation_type || '-'}</td>
              <td>{sig.baud_rate || '-'}</td>
              <td>{sig.likely_purpose || '-'}</td>
              <td>{sig.label || '-'}</td>
              <td>{sig.protocol_name || sig.protocol?.name || '-'}</td>
              <td>{sig.signal_strength}</td>
              <td>{sig.duration}</td>
              <td>
                <a
                  className="iq-download"
                  href={`/api/signals/${sig.id}/export`}
                  title="Download raw 32-bit float I/Q data (.complex)"
                  onClick={(e) => e.stopPropagation()}
                >
                  ⬇️ Export I/Q
                </a>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default DetectedSignalsPanel;
