const isLocalDevHost = (host) => (
  host === 'localhost'
  || host === '127.0.0.1'
  || host === '0.0.0.0'
);

export function resolveApiBaseUrl() {
  if (import.meta.env.VITE_API_BASE_URL) {
    return import.meta.env.VITE_API_BASE_URL;
  }
  const { protocol, hostname, port, origin } = window.location;
  const isDevServer = port === '5173' || port === '4173';
  if (isDevServer && isLocalDevHost(hostname)) {
    return `${protocol}//127.0.0.1:8000`;
  }
  return origin;
}

export function buildWebSocketUrl(pathname, query = '') {
  const wsBase = import.meta.env.VITE_WS_BASE_URL || resolveApiBaseUrl();
  const wsProtocol = wsBase.startsWith('https') ? 'wss' : 'ws';
  const wsHost = wsBase.replace(/^https?:\/\//, '');
  const normalizedPath = pathname.startsWith('/') ? pathname : `/${pathname}`;
  return `${wsProtocol}://${wsHost}${normalizedPath}${query}`;
}
