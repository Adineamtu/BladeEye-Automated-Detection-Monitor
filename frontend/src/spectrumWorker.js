let ws = null;
let reconnectTimer = null;
let disposed = false;
let usingBinary = true;
let endpointBase = '';
let maxFps = 20;
let frameIntervalMs = 50;
let nextEmitAt = 0;
let latestPending = null;
let frameReady = true;

const scheduleReconnect = () => {
  if (disposed || reconnectTimer) return;
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    connect(true);
  }, 1000);
};

const emitFrameIfPossible = () => {
  if (!frameReady || !latestPending) return;
  const now = Date.now();
  if (now < nextEmitAt) {
    return;
  }
  const frame = latestPending;
  latestPending = null;
  nextEmitAt = now + frameIntervalMs;
  frameReady = false;
  self.postMessage({ type: 'spectrum', frame }, [frame.buffer]);
};

const queueFrame = (arr) => {
  latestPending = arr;
  emitFrameIfPossible();
};

const connect = (binary) => {
  if (disposed) return;
  usingBinary = binary;
  const wsProtocol = endpointBase.startsWith('https') ? 'wss' : 'ws';
  const wsHost = endpointBase.replace(/^https?:\/\//, '');
  const endpoint = binary ? '/ws/spectrum/binary' : '/ws/spectrum';
  ws = new WebSocket(`${wsProtocol}://${wsHost}${endpoint}?fps=${maxFps}`);
  if (binary) ws.binaryType = 'arraybuffer';

  ws.onopen = () => self.postMessage({ type: 'socket', connected: true });
  ws.onerror = () => self.postMessage({ type: 'socket', connected: false });
  ws.onclose = () => {
    self.postMessage({ type: 'socket', connected: false });
    if (binary) {
      connect(false);
    } else {
      scheduleReconnect();
    }
  };
  ws.onmessage = (event) => {
    if (usingBinary && event.data instanceof ArrayBuffer) {
      queueFrame(new Float32Array(event.data));
      return;
    }
    try {
      const parsed = JSON.parse(event.data);
      queueFrame(Float32Array.from(parsed));
    } catch {
      // drop malformed payloads
    }
  };
};

self.onmessage = (event) => {
  const msg = event.data || {};
  if (msg.type === 'init') {
    endpointBase = msg.endpointBase;
    maxFps = Math.max(5, Math.min(30, Number(msg.maxFps) || 20));
    frameIntervalMs = 1000 / maxFps;
    disposed = false;
    connect(true);
    return;
  }
  if (msg.type === 'frame_processed') {
    frameReady = true;
    emitFrameIfPossible();
    return;
  }
  if (msg.type === 'dispose') {
    disposed = true;
    if (reconnectTimer) clearTimeout(reconnectTimer);
    reconnectTimer = null;
    if (ws) ws.close();
  }
};
