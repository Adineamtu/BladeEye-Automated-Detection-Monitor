# API Contract

This document describes the HTTP and WebSocket interfaces provided by the
backend server.  All routes are rooted at `/api` unless stated otherwise.
CORS is enabled with permissive defaults to ease development with the React
frontend.

## REST endpoints

### `GET /api/signals`
Returns a list of currently detected signals.  Each entry has the following
shape:

```json
{
  "center_frequency": 433920000.0,
  "modulation_type": "FSK",
  "baud_rate": 4800.0,
  "signal_strength": -15.2,
  "duration": 0.25
}
```

### `GET /api/watchlist`
Retrieve the current list of watchlisted center frequencies (in Hz).

### `POST /api/watchlist`
Append a frequency to the watchlist.

```json
{
  "frequency": 433920000.0
}
```

### `DELETE /api/watchlist/{frequency}`
Remove a frequency from the watchlist.

### `GET /api/session`
Return the current in-memory session consisting of detected signals and the
watchlist.

Response body:

```json
{
  "signals": [SignalPayload, ...],
  "watchlist": [433920000.0]
}
```

`SignalPayload` has the same fields described in `GET /api/signals`.

### `POST /api/session`
Replace the current in-memory session.  The request body matches the response
schema from `GET /api/session`.

### `POST /api/scan/start`
Start or resume the SDR monitor. Returns the running state:

```json
{ "is_running": true }
```

Returns HTTP 409 if the monitor is already running.

### `POST /api/scan/stop`
Stop the SDR monitor. Returns the running state:

```json
{ "is_running": false }
```

Returns HTTP 409 if the monitor is already stopped.

## WebSocket endpoint

### `GET /ws/spectrum`
Streams FFT power spectra as JSON arrays.  Frames are emitted roughly every
100 ms (10 Hz).  When an SDR `PassiveMonitor` is attached the spectra represent
live data; otherwise random noise is sent for development purposes.

