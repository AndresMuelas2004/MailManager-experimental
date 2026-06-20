/**
 * Integration tests for ``useBulkBar`` — multi-page selection.
 *
 * The hook keeps a ``Map<key, EmailMetadataOut>`` so a selection made on
 * one page survives onto another (the underlying ``useSelection`` Set
 * only stores keys, not the email objects the bulk fan-out needs). The
 * wiring trap these tests guard: ``useBulkBar`` must RETURN a selection
 * whose ``toggle`` / ``toggleTopN`` / ``clear`` are the wrapped versions
 * that also mutate the Map — if it returned the raw ``useSelection``
 * object, the pages (which call ``selection.toggle`` directly) would
 * bypass the Map and the count would collapse when leaving a page.
 *
 * ``useBulkBar`` mounts ``BulkActionsBar`` (which renders "{n}
 * seleccionado(s)") and uses ``useEmailBulkActions`` (which calls
 * ``useQueryClient``), so the harness wraps it in a QueryClientProvider
 * and renders the returned ``bulkBar`` node to read the Map-derived count.
 */

import { type ReactNode } from 'react';
import { QueryClientProvider } from '@tanstack/react-query';
import { act, render, renderHook, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import useBulkBar from './useBulkBar';
import { createTestQueryClient } from '../../../test/renderWithProviders';
import { I18nProvider } from '../../../lib/i18n';
import { pinTestLang } from '../../../test/i18nTestLang';
import type { EmailMetadataOut } from '../../../api/types/dto';

// The bulk bar's "{n} seleccionado(s)" label comes from ``t()``; pin Spanish so
// the existing ``/seleccionado/`` assertion holds (jsdom defaults to English).
pinTestLang('es');

function makeEmail(id: string, overrides: Partial<EmailMetadataOut> = {}): EmailMetadataOut {
  return {
    provider_message_id: id,
    account_id: 'a_1',
    mailbox_id: 'mb_1',
    thread_id: null,
    from_email: `${id}@example.com`,
    from_name: id,
    to_email: null,
    to_name: null,
    subject: `Subject ${id}`,
    received_at: '2024-01-01T00:00:00Z',
    is_read: false,
    box: 'ALL_MAIL',
    has_attachments: false,
    is_favorite: false,
    thread_message_count: 1,
    ...overrides,
  };
}

function wrapper({ children }: { children: ReactNode }) {
  const client = createTestQueryClient();
  return (
    <QueryClientProvider client={client}>
      <I18nProvider>{children}</I18nProvider>
    </QueryClientProvider>
  );
}

const noopRefresh = () => Promise.resolve();

// Render the bar node currently returned by the hook and read the
// "{n} seleccionado(s)" label the Map drives.
function selectedCountFromBar(node: ReactNode): number {
  // The bar node carries ``t()`` calls, so it must render inside I18nProvider.
  const { unmount } = render(<I18nProvider>{node}</I18nProvider>);
  const label = screen.getByText(/seleccionado/);
  const n = parseInt(label.textContent ?? '0', 10);
  unmount();
  return n;
}

describe('useBulkBar — multi-page selection', () => {
  it('retains selected emails from a previous page (Map persists across pages)', () => {
    const page1Email = makeEmail('p1_a');
    const page2Email = makeEmail('p2_a');

    const { result } = renderHook(() => useBulkBar({ box: 'ALL_MAIL', refresh: noopRefresh }), {
      wrapper,
    });

    // Select a row from "page 1" through the wrapped toggle the hook returns.
    act(() => result.current.selection.toggle(page1Email));
    expect(result.current.selection.size).toBe(1);
    expect(selectedCountFromBar(result.current.bulkBar)).toBe(1);

    // Now select a row from "page 2" — the page-1 row is no longer on
    // screen, but both the Set and the Map-derived count must reflect 2.
    act(() => result.current.selection.toggle(page2Email));
    expect(result.current.selection.size).toBe(2);
    expect(selectedCountFromBar(result.current.bulkBar)).toBe(2);
  });

  it('"select all" marks only the current page and toggles off on a second call', () => {
    const page = [makeEmail('a'), makeEmail('b'), makeEmail('c')];

    const { result } = renderHook(() => useBulkBar({ box: 'ALL_MAIL', refresh: noopRefresh }), {
      wrapper,
    });

    act(() => result.current.selection.toggleTopN(page));
    expect(result.current.selection.size).toBe(3);
    expect(selectedCountFromBar(result.current.bulkBar)).toBe(3);
    expect(result.current.selection.headerState(page)).toBe('checked');

    // A second toggleTopN clears everything (mirrors useSelection).
    act(() => result.current.selection.toggleTopN(page));
    expect(result.current.selection.size).toBe(0);
  });

  it('clears the selection (Set + Map) when the search key changes', () => {
    const email = makeEmail('a');

    const { result, rerender } = renderHook(
      ({ searchKey }: { searchKey: string }) =>
        useBulkBar({ box: 'ALL_MAIL', refresh: noopRefresh, searchKey }),
      { wrapper, initialProps: { searchKey: '' } },
    );

    act(() => result.current.selection.toggle(email));
    expect(result.current.selection.size).toBe(1);

    // Changing the debounced search term must wipe the selection so a
    // stale selection does not survive into a filtered result.
    rerender({ searchKey: 'invoice' });
    expect(result.current.selection.size).toBe(0);
    expect(selectedCountFromBar(result.current.bulkBar)).toBe(0);
  });

  it('derives the read-toggle target from every selected row, not just the visible page', () => {
    // Two read rows + one unread row → unread (1) < read (2), so the
    // suggested action is "mark unread".
    const a = makeEmail('a', { is_read: true });
    const b = makeEmail('b', { is_read: true });
    const c = makeEmail('c', { is_read: false });

    const { result } = renderHook(() => useBulkBar({ box: 'ALL_MAIL', refresh: noopRefresh }), {
      wrapper,
    });

    act(() => {
      result.current.selection.toggle(a);
      result.current.selection.toggle(b);
      result.current.selection.toggle(c);
    });

    render(<I18nProvider>{result.current.bulkBar}</I18nProvider>);
    expect(screen.getByText('Marcar como no leídos')).toBeInTheDocument();
  });
});
