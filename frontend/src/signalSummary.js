import fskIcon from './assets/fsk.svg';
import pskIcon from './assets/psk.svg';

const summaryText = {
  FSK: (signal, deviation = {}) => {
    const rate = signal.baud_rate ? `${signal.baud_rate} baud` : 'unknown baud rate';
    const devs = deviation.deviations || [];
    const maxDev = devs.length ? Math.max(...devs.map((d) => Math.abs(d))) : null;
    const hop = maxDev !== null ? `frequency hopping ~${maxDev.toFixed(1)} Hz` : 'no deviation data';
    return {
      text: `FSK at ${rate}, ${hop}.`,
      icon: fskIcon,
    };
  },
  PSK: (signal, _deviation, iq = []) => {
    const rate = signal.baud_rate ? `${signal.baud_rate} baud` : 'unknown baud rate';
    let clarity = 'no I/Q samples';
    if (iq.length) {
      const mags = iq.map(([i, q]) => Math.sqrt(i * i + q * q));
      const avg = mags.reduce((a, b) => a + b, 0) / mags.length;
      const variance = mags.reduce((a, b) => a + (b - avg) ** 2, 0) / mags.length;
      clarity = variance < 0.5 ? 'clear constellation' : 'noisy constellation';
    }
    return {
      text: `PSK at ${rate}, ${clarity}.`,
      icon: pskIcon,
    };
  },
};

export function getSummaryText(signal, deviation, iq) {
  if (!signal) return { text: '', icon: null };
  const mod = signal.modulation_type;
  const handler = summaryText[mod];
  if (handler) return handler(signal, deviation, iq);
  const rate = signal.baud_rate ? `${signal.baud_rate} baud` : 'unknown baud rate';
  return { text: `Unknown modulation at ${rate}.`, icon: null };
}

export default getSummaryText;
