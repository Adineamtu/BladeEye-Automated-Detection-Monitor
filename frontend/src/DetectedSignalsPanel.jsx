import './DetectedSignalsPanel.css';

function DetectedSignalsPanel({ signals, onSelect, alertThreshold }) {
  const saveAsSignature = async (sig, e) => {
    e.stopPropagation();
    const name = window.prompt('Nume semnătură nouă (ex: Barieră Garaj Vecin):');
    if (!name) return;
    try {
      const res = await fetch('/api/signatures/capture', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name,
          short_pulse: sig.short_pulse,
          long_pulse: sig.long_pulse,
          gap: sig.gap,
          modulation: sig.modulation_type,
        }),
      });
      if (!res.ok) throw new Error('Save failed');
      window.alert('Semnătură salvată cu succes.');
    } catch (err) {
      window.alert('Nu s-a putut salva semnătura.');
    }
  };

  return (
    <div className="signals-panel">
      <h2>Detected Signals</h2>
      <table className="signals-table">
        <thead>
          <tr>
            <th>Center Frequency</th>
            <th>Modulation Type</th>
            <th>Baud Rate</th>
            <th>Detection</th>
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
              <td>{sig.detection_status || '-'}</td>
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
                {sig.detection_status?.includes('Unknown Signal') &&
                  sig.short_pulse != null &&
                  sig.long_pulse != null && (
                    <button className="iq-download" onClick={(e) => saveAsSignature(sig, e)}>
                      Save as Signature
                    </button>
                  )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default DetectedSignalsPanel;
