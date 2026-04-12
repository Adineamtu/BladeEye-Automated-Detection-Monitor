import { describe, test, expect } from 'vitest';
import { getSummaryText } from './signalSummary';

describe('getSummaryText', () => {
  test('describes FSK with deviation data', () => {
    const signal = { modulation_type: 'FSK', baud_rate: 1200 };
    const deviation = { deviations: [1, 3, 2] };
    const { text, icon } = getSummaryText(signal, deviation, []);
    expect(text).toContain('1200 baud');
    expect(text).toMatch(/frequency hopping ~3\.0/);
    expect(icon).toBeTruthy();
  });

  test('describes PSK with IQ data', () => {
    const signal = { modulation_type: 'PSK', baud_rate: 4800 };
    const iq = [ [1, 0], [0.9, 0.1], [1.1, -0.1] ];
    const { text, icon } = getSummaryText(signal, {}, iq);
    expect(text).toContain('4800 baud');
    expect(text).toMatch(/constellation/);
    expect(icon).toBeTruthy();
  });

  test('handles unknown modulation', () => {
    const signal = { modulation_type: 'QAM', baud_rate: 9600 };
    const { text, icon } = getSummaryText(signal, {}, []);
    expect(text).toContain('Unknown modulation');
    expect(icon).toBeNull();
  });
});
