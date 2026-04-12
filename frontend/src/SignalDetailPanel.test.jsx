import { render, screen, within, fireEvent, cleanup, waitFor, act } from '@testing-library/react';
import { vi, test, expect, afterEach, beforeAll } from 'vitest';
import '@testing-library/jest-dom/vitest';
import SignalDetailPanel, { parseIQBuffer } from './SignalDetailPanel';

afterEach(() => {
  cleanup();
});

beforeAll(() => {
  global.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
});

function noop() {}

test('parseIQBuffer converts arrayBuffer to I/Q pairs', () => {
  const arr = new Float32Array([1, 2, 3, 4]);
  expect(parseIQBuffer(arr.buffer)).toEqual([
    [1, 2],
    [3, 4]
  ]);
});

test('renders constellation plot for PSK signals', async () => {
  const iqData = new Float32Array([1, -1, 0.5, 0.5]);
  vi.spyOn(global, 'fetch').mockImplementation((url) => {
    if (url.endsWith('/power')) {
      return Promise.resolve({ ok: true, json: async () => ({ times: [], powers: [] }) });
    }
    if (url.endsWith('/baud')) {
      return Promise.resolve({ ok: true, json: async () => ({ hist: [], bins: [] }) });
    }
    if (url.endsWith('/iq')) {
      return Promise.resolve({ ok: true, arrayBuffer: async () => iqData.buffer });
    }
    return Promise.reject(new Error('unknown url'));
  });

  render(
    <SignalDetailPanel
      signal={{ center_frequency: 123, modulation_type: 'PSK' }}
      watchlist={[]}
      onClose={noop}
      onAddWatchlist={noop}
      onRemoveWatchlist={noop}
    />
  );

  const plot = await screen.findByTestId('constellation-plot');
  const points = within(plot).getAllByTestId('iq-point');
  expect(points).toHaveLength(2);
  global.fetch.mockRestore();
});

test('clicking Download I/Q triggers file download', async () => {
  const fetchMock = vi.spyOn(global, 'fetch').mockImplementation((url) => {
    if (url.endsWith('/power')) {
      return Promise.resolve({ ok: true, json: async () => ({ times: [], powers: [] }) });
    }
    if (url.endsWith('/baud')) {
      return Promise.resolve({ ok: true, json: async () => ({ hist: [], bins: [] }) });
    }
    if (url.endsWith('/iq')) {
      return Promise.resolve({
        ok: true,
        arrayBuffer: async () => new ArrayBuffer(0),
        blob: async () => new Blob(['data']),
      });
    }
    return Promise.reject(new Error('unknown url'));
  });

  const clickSpy = vi.fn();
  const realCreateElement = document.createElement.bind(document);
  vi.spyOn(document, 'createElement').mockImplementation((tag) => {
    if (tag === 'a') {
      return { href: '', download: '', click: clickSpy };
    }
    return realCreateElement(tag);
  });
  const origCreate = window.URL.createObjectURL;
  const origRevoke = window.URL.revokeObjectURL;
  window.URL.createObjectURL = vi.fn(() => 'blob:mock');
  window.URL.revokeObjectURL = vi.fn();

  render(
    <SignalDetailPanel
      signal={{ center_frequency: 456, modulation_type: 'PSK' }}
      watchlist={[]}
      onClose={noop}
      onAddWatchlist={noop}
      onRemoveWatchlist={noop}
    />
  );

  const btn = await screen.findByRole('button', { name: /download i\/q/i });
  fireEvent.click(btn);

  expect(fetchMock).toHaveBeenCalledWith('/api/signals/456/iq');
  await waitFor(() => expect(clickSpy).toHaveBeenCalled());

  global.fetch.mockRestore();
  document.createElement.mockRestore();
  window.URL.createObjectURL = origCreate;
  window.URL.revokeObjectURL = origRevoke;
});

test('switches between binary, hex and ascii representations', async () => {
  const fetchMock = vi.spyOn(global, 'fetch').mockImplementation((url) => {
    if (url.endsWith('/power')) {
      return Promise.resolve({ ok: true, json: async () => ({ times: [], powers: [] }) });
    }
    if (url.endsWith('/baud')) {
      return Promise.resolve({ ok: true, json: async () => ({ hist: [], bins: [] }) });
    }
    return Promise.reject(new Error('unknown url'));
  });

  const origClipboard = navigator.clipboard;
  const writeMock = vi.fn();
  Object.assign(navigator, { clipboard: { writeText: writeMock } });

  render(
    <SignalDetailPanel
      signal={{ center_frequency: 789, modulation_type: 'OOK' }}
      watchlist={[]}
      onClose={noop}
      onAddWatchlist={noop}
      onRemoveWatchlist={noop}
      initialDecoded={{ binary: '01000001', hex: '41', ascii: 'A' }}
    />
  );

  const out = await screen.findByTestId('decoded-output');
  expect(out).toHaveTextContent('01000001');

  fireEvent.click(screen.getByRole('button', { name: /hex/i }));
  expect(out).toHaveTextContent('41');

  fireEvent.click(screen.getByRole('button', { name: /ascii/i }));
  expect(out).toHaveTextContent('A');

  fireEvent.click(screen.getByRole('button', { name: /copy/i }));
  expect(writeMock).toHaveBeenCalledWith('A');

  fetchMock.mockRestore();
  navigator.clipboard = origClipboard;
});

test('renders frequency time chart for FSK signals', async () => {
  const origFetch = global.fetch;
  const fetchMock = vi.fn((url) => {
    if (url.endsWith('/power')) {
      return Promise.resolve({ ok: true, json: async () => ({ times: [], powers: [] }) });
    }
    if (url.endsWith('/baud')) {
      return Promise.resolve({ ok: true, json: async () => ({ hist: [], bins: [] }) });
    }
    if (url.endsWith('/deviation')) {
      return Promise.resolve({ ok: true, json: async () => ({ times: [], deviations: [] }) });
    }
    if (url.endsWith('/trace')) {
      return Promise.resolve({ ok: true, json: async () => ({ times: [0, 1], frequencies: [1, 2] }) });
    }
    return Promise.reject(new Error('unknown url'));
  });
  global.fetch = fetchMock;

  render(
    <SignalDetailPanel
      signal={{ center_frequency: 123, modulation_type: 'FSK' }}
      watchlist={[]}
      onClose={noop}
      onAddWatchlist={noop}
      onRemoveWatchlist={noop}
      config={{ hopping_enabled: false }}
    />
  );

  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/signals/123/trace'));
  global.fetch = origFetch;
});

test('renders frequency time chart when hopping enabled', async () => {
  const origFetch = global.fetch;
  const fetchMock = vi.fn((url) => {
    if (url.endsWith('/power')) {
      return Promise.resolve({ ok: true, json: async () => ({ times: [], powers: [] }) });
    }
    if (url.endsWith('/baud')) {
      return Promise.resolve({ ok: true, json: async () => ({ hist: [], bins: [] }) });
    }
    if (url.endsWith('/trace')) {
      return Promise.resolve({ ok: true, json: async () => ({ times: [0], frequencies: [3] }) });
    }
    return Promise.reject(new Error('unknown url'));
  });
  global.fetch = fetchMock;

  render(
    <SignalDetailPanel
      signal={{ center_frequency: 456, modulation_type: 'OOK' }}
      watchlist={[]}
      onClose={noop}
      onAddWatchlist={noop}
      onRemoveWatchlist={noop}
      config={{ hopping_enabled: true }}
    />
  );

  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/signals/456/trace'));
  global.fetch = origFetch;
});
