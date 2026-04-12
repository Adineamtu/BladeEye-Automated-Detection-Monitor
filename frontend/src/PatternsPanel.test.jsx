import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react';
import { describe, test, expect, vi, afterEach } from 'vitest';
import '@testing-library/jest-dom/vitest';
import PatternsPanel from './PatternsPanel';

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function mockFetch(responses) {
  global.fetch = vi.fn();
  responses.forEach((res) => {
    fetch.mockResolvedValueOnce({ ok: true, json: async () => res });
  });
}

describe('PatternsPanel', () => {
  test('renders patterns from API', async () => {
    mockFetch([{ foo: {}, bar: {} }]);
    render(<PatternsPanel />);
    expect(fetch).toHaveBeenCalledWith('/api/patterns');
    await screen.findByText('foo');
    await screen.findByText('bar');
  });

  test('rename and delete operations', async () => {
    mockFetch([{ foo: {} }, {}, {}]);
    vi.spyOn(window, 'prompt').mockReturnValue('baz');
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    render(<PatternsPanel />);
    const renameBtn = await screen.findByText('Rename');
    fireEvent.click(renameBtn);
    await waitFor(() =>
      expect(fetch).toHaveBeenCalledWith(
        '/api/patterns/foo',
        expect.objectContaining({ method: 'PUT' })
      )
    );
    await screen.findByText('baz');
    const deleteBtn = screen.getByText('Delete');
    fireEvent.click(deleteBtn);
    await waitFor(() =>
      expect(fetch).toHaveBeenCalledWith(
        '/api/patterns/baz',
        expect.objectContaining({ method: 'DELETE' })
      )
    );
    await waitFor(() => expect(screen.queryByText('baz')).not.toBeInTheDocument());
  });
});
