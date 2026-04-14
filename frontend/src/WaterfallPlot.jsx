import {
  useEffect,
  useRef,
  useState,
} from 'react';
import { scaleSequential } from 'd3-scale';
import { interpolateTurbo } from 'd3-scale-chromatic';

const CANVAS_HEIGHT = 200; // number of rows to retain

function WaterfallPlot({
  watchlist = [],
  signals = [],
  onCursorMove,
  onSelectSignal,
  onSpectrumFrame,
  onSocketStateChange,
}) {
  const canvasRef = useRef(null);
  const colorScale = scaleSequential(interpolateTurbo).domain([0, 1]);
  const watchlistRef = useRef(watchlist);
  const [markers, setMarkers] = useState([]);
  const markersRef = useRef(markers);
  const [freqWindow, setFreqWindow] = useState({ start: 0, end: 512 });
  const windowRef = useRef(freqWindow);
  const dataLenRef = useRef(512);
  const latestSpectrumRef = useRef([]);
  const [cursor, setCursor] = useState(null);

  useEffect(() => {
    watchlistRef.current = watchlist;
  }, [watchlist]);

  useEffect(() => {
    markersRef.current = markers;
  }, [markers]);

  useEffect(() => {
    windowRef.current = freqWindow;
  }, [freqWindow]);

  const clampWindow = (start, end) => {
    const dataLen = dataLenRef.current;
    const span = end - start;
    let newStart = start;
    let newEnd = end;
    if (span < 10) {
      newEnd = newStart + 10;
    }
    if (newStart < 0) {
      newStart = 0;
      newEnd = newStart + span;
    }
    if (newEnd > dataLen) {
      newEnd = dataLen;
      newStart = newEnd - span;
    }
    if (newStart < 0) {
      newStart = 0;
    }
    return { start: newStart, end: newEnd };
  };

  const zoom = (direction) => {
    const { start, end } = windowRef.current;
    const span = end - start;
    const zoomFactor = 0.1;
    let newSpan = span * (1 + zoomFactor * direction);
    const dataLen = dataLenRef.current;
    newSpan = Math.max(10, Math.min(newSpan, dataLen));
    const center = start + span / 2;
    let newStart = center - newSpan / 2;
    let newEnd = center + newSpan / 2;
    ({ start: newStart, end: newEnd } = clampWindow(newStart, newEnd));
    setFreqWindow({ start: newStart, end: newEnd });
  };

  const pan = (fraction) => {
    const { start, end } = windowRef.current;
    const span = end - start;
    const shift = span * fraction;
    let newStart = start + shift;
    let newEnd = end + shift;
    ({ start: newStart, end: newEnd } = clampWindow(newStart, newEnd));
    setFreqWindow({ start: newStart, end: newEnd });
  };

  const handleWheel = (e) => {
    e.preventDefault();
    zoom(e.deltaY > 0 ? 1 : -1);
  };

  const draggingRef = useRef(false);
  const lastXRef = useRef(0);

  const handleMouseDown = (e) => {
    draggingRef.current = true;
    lastXRef.current = e.clientX;
  };

  const handleMouseMove = (e) => {
    const canvas = canvasRef.current;
    if (draggingRef.current) {
      const dx = e.clientX - lastXRef.current;
      lastXRef.current = e.clientX;
      pan(-dx / canvas.width);
      return;
    }
    const rect = canvas.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const width = canvas.width;
    const { start, end } = windowRef.current;
    const span = end - start;
    const freq = start + (x / width) * span;
    const idx = Math.floor(freq);
    const power = latestSpectrumRef.current[idx];
    setCursor({ x, freq, power });
    if (onCursorMove) onCursorMove(freq, power);
  };

  const handleClick = (e) => {
    const canvas = canvasRef.current;
    const rect = canvas.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const width = canvas.width;
    const { start, end } = windowRef.current;
    const span = end - start;
    const data = latestSpectrumRef.current;
    const idx = Math.floor(start + (x / width) * span);
    const windowSize = 5;
    let peakIdx = idx;
    let peakVal = -Infinity;
    for (
      let i = Math.max(0, idx - windowSize);
      i <= Math.min(data.length - 1, idx + windowSize);
      i += 1
    ) {
      const val = data[i];
      if (val !== undefined && val > peakVal) {
        peakVal = val;
        peakIdx = i;
      }
    }
    const freq = peakIdx;
    let chosen = null;
    let minDiff = Infinity;
    signals.forEach((sig) => {
      const diff = Math.abs(sig.center_frequency - freq);
      if (diff < minDiff) {
        minDiff = diff;
        chosen = sig;
      }
    });
    if (chosen && minDiff <= windowSize) {
      setMarkers((prev) =>
        prev.includes(chosen.center_frequency)
          ? prev
          : [...prev, chosen.center_frequency],
      );
      if (onSelectSignal) onSelectSignal(chosen);
    }
  };

  const handleContextMenu = (e) => {
    e.preventDefault();
    const canvas = canvasRef.current;
    const rect = canvas.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const width = canvas.width;
    const { start, end } = windowRef.current;
    const span = end - start;
    const freq = start + (x / width) * span;
    const threshold = 5;
    setMarkers((prev) => {
      const idx = prev.findIndex((f) => Math.abs(f - freq) <= threshold);
      if (idx !== -1) {
        const next = [...prev];
        next.splice(idx, 1);
        return next;
      }
      return prev;
    });
  };

  const stopDragging = () => {
    draggingRef.current = false;
  };

  const handleMouseLeave = () => {
    stopDragging();
    setCursor(null);
    if (onCursorMove) onCursorMove(null, null);
  };

  const handleKeyDown = (e) => {
    if (e.key === '+' || e.key === '=') {
      zoom(-1);
    } else if (e.key === '-' || e.key === '_') {
      zoom(1);
    } else if (e.key === 'ArrowLeft') {
      pan(-0.1);
    } else if (e.key === 'ArrowRight') {
      pan(0.1);
    }
  };

  useEffect(() => {
    const canvas = canvasRef.current;
    const ctx = canvas.getContext('2d');
    const wsProtocol = window.location.protocol === 'https:' ? 'wss' : 'ws';
    let ws;
    let usingBinary = true;
    let reconnectTimer = null;
    let disposed = false;
    const rowHeight = 1; // each spectrum occupies one pixel vertically
    const handleSpectrumData = (data) => {
      if (onSpectrumFrame) onSpectrumFrame();
      latestSpectrumRef.current = data;
      dataLenRef.current = data.length;
      const width = canvas.width;
      const height = canvas.height;
      const { start, end } = windowRef.current;
      const span = end - start;

      // Scroll existing image up by one row to make room for new data
      const imageData = ctx.getImageData(0, rowHeight, width, height - rowHeight);
      ctx.putImageData(imageData, 0, 0);

      // Draw new spectrum line at the bottom
      for (let x = 0; x < width; x += 1) {
        const idx = Math.floor(start + (x / width) * span);
        const val = data[idx] ?? 0;
        ctx.fillStyle = colorScale(val);
        ctx.fillRect(x, height - rowHeight, 1, rowHeight);
      }

      // Overlay watchlist markers with high-contrast lines
      watchlistRef.current.forEach((f) => {
        if (f >= start && f <= end) {
          const idx = Math.round(((f - start) / span) * width);
          ctx.fillStyle = '#ffffff';
          ctx.fillRect(idx, 0, 1, height);
        }
      });

      // Overlay user placed markers
      markersRef.current.forEach((f) => {
        if (f >= start && f <= end) {
          const idx = Math.round(((f - start) / span) * width);
          ctx.fillStyle = '#ff0000';
          ctx.fillRect(idx, 0, 1, height);
        }
      });
    };
    const scheduleReconnect = (binary) => {
      if (disposed || reconnectTimer) return;
      reconnectTimer = setTimeout(() => {
        reconnectTimer = null;
        if (!disposed) connect(binary);
      }, 1000);
    };

    const connect = (binary) => {
      usingBinary = binary;
      const endpoint = binary ? '/ws/spectrum/binary' : '/ws/spectrum';
      ws = new WebSocket(`${wsProtocol}://${window.location.host}${endpoint}`);
      if (binary) {
        ws.binaryType = 'arraybuffer';
      }
      ws.onopen = () => {
        if (onSocketStateChange) onSocketStateChange(true);
      };
      ws.onclose = () => {
        if (onSocketStateChange) onSocketStateChange(false);
        if (binary) {
          connect(false);
        } else {
          scheduleReconnect(true);
        }
      };
      ws.onerror = () => {
        if (onSocketStateChange) onSocketStateChange(false);
      };
      ws.onmessage = (event) => {
        if (usingBinary && event.data instanceof ArrayBuffer) {
          handleSpectrumData(Array.from(new Float32Array(event.data)));
          return;
        }
        try {
          handleSpectrumData(JSON.parse(event.data));
        } catch (err) {
          // Ignore malformed frames and keep stream alive.
        }
      };
    };

    connect(true);

    return () => {
      disposed = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      if (ws) ws.close();
    };
  }, [colorScale, onSocketStateChange, onSpectrumFrame]);

  return (
    <div style={{ position: 'relative', width: '100%', height: CANVAS_HEIGHT }}>
      <canvas
        ref={canvasRef}
        width={512}
        height={CANVAS_HEIGHT}
        style={{ width: '100%', height: CANVAS_HEIGHT }}
        tabIndex={0}
        onWheel={handleWheel}
        onMouseDown={handleMouseDown}
        onMouseMove={handleMouseMove}
        onMouseUp={stopDragging}
        onMouseLeave={handleMouseLeave}
        onKeyDown={handleKeyDown}
        onClick={handleClick}
        onContextMenu={handleContextMenu}
      />
      {cursor && (
        <>
          <div
            style={{
              position: 'absolute',
              top: 0,
              bottom: 0,
              left: cursor.x,
              width: 1,
              background: '#fff',
              pointerEvents: 'none',
            }}
          />
          <div
            style={{
              position: 'absolute',
              left: cursor.x + 8,
              top: 8,
              background: 'rgba(0,0,0,0.7)',
              color: '#fff',
              padding: '2px 4px',
              fontSize: 12,
              pointerEvents: 'none',
            }}
          >
            <div>{`f: ${cursor.freq.toFixed(2)}`}</div>
            <div>{`p: ${cursor.power !== undefined ? cursor.power.toFixed(2) : 'N/A'}`}</div>
          </div>
        </>
      )}
    </div>
  );
}

export default WaterfallPlot;
