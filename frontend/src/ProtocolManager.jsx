import { useEffect, useState } from 'react';

function ProtocolManager() {
  const [protocols, setProtocols] = useState([]);
  const [form, setForm] = useState({
    protocol_name: '',
    modulation_type: '',
    baud_rate: '',
    header_pattern: '',
    data_field_structure: '{}',
  });

  async function fetchProtocols() {
    try {
      const res = await fetch('/api/protocols');
      if (res.ok) setProtocols(await res.json());
    } catch (err) {
      console.error('Failed to fetch protocols', err);
    }
  }

  useEffect(() => {
    fetchProtocols();
  }, []);

  function handleChange(e) {
    const { name, value } = e.target;
    setForm((prev) => ({ ...prev, [name]: value }));
  }

  async function handleSubmit(e) {
    e.preventDefault();
    let structure;
    try {
      structure = JSON.parse(form.data_field_structure || '{}');
    } catch (err) {
      alert('data_field_structure must be valid JSON');
      return;
    }
    const payload = {
      protocol_name: form.protocol_name,
      modulation_type: form.modulation_type,
      baud_rate: Number(form.baud_rate),
      header_pattern: form.header_pattern,
      data_field_structure: structure,
    };
    try {
      const res = await fetch('/api/protocols/add_manual', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (res.ok) {
        setForm({ protocol_name: '', modulation_type: '', baud_rate: '', header_pattern: '', data_field_structure: '{}' });
        fetchProtocols();
      }
    } catch (err) {
      console.error('Failed to save protocol', err);
    }
  }

  return (
    <div className="protocol-manager">
      <h2>Protocols</h2>
      <table>
        <thead>
          <tr>
            <th>Name</th>
            <th>Modulation</th>
            <th>Baud</th>
            <th>Header</th>
            <th>Fields</th>
          </tr>
        </thead>
        <tbody>
          {protocols.map((p, idx) => (
            <tr key={idx}>
              <td>{p.protocol_name}</td>
              <td>{p.modulation_type || '-'}</td>
              <td>{p.baud_rate || '-'}</td>
              <td>{p.header_pattern}</td>
              <td>{JSON.stringify(p.data_field_structure)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <h3>Add Protocol</h3>
      <form onSubmit={handleSubmit} className="protocol-form">
        <input
          type="text"
          name="protocol_name"
          placeholder="Name"
          value={form.protocol_name}
          onChange={handleChange}
          required
        />
        <input
          type="text"
          name="modulation_type"
          placeholder="Modulation"
          value={form.modulation_type}
          onChange={handleChange}
          required
        />
        <input
          type="number"
          name="baud_rate"
          placeholder="Baud Rate"
          value={form.baud_rate}
          onChange={handleChange}
          required
        />
        <input
          type="text"
          name="header_pattern"
          placeholder="Header Pattern"
          value={form.header_pattern}
          onChange={handleChange}
          required
        />
        <textarea
          name="data_field_structure"
          placeholder='{"field": [start, length]}'
          value={form.data_field_structure}
          onChange={handleChange}
        />
        <button type="submit">Add</button>
      </form>
    </div>
  );
}

export default ProtocolManager;
