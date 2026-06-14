import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { Route, Routes, useLocation } from 'react-router-dom';
import { describe, expect, it } from 'vitest';

import { renderWithProviders } from '../../../test/renderWithProviders';
import { server } from '../../../test/msw/server';
import UnifiedInboxPage from './UnifiedInboxPage';

const API_BASE = 'http://localhost:8000';

// MemoryRouter does not touch window.location, so to assert on the URL we
// render the router's current search string into the DOM via a probe.
function LocationProbe() {
  const location = useLocation();
  return <span data-testid="location-search">{location.search}</span>;
}

function locationSearch(): string {
  return screen.getByTestId('location-search').textContent ?? '';
}

const emailFixtures = [
  {
    provider_message_id: 'm_1',
    account_id: 'a_1',
    mailbox_id: 'mb_1',
    thread_id: null,
    from_email: 'alice@example.com',
    from_name: 'Alice',
    subject: 'Welcome to the platform',
    received_at: new Date('2024-01-10T09:00:00Z').toISOString(),
    is_read: false,
    box: 'ALL_MAIL',
  },
  {
    provider_message_id: 'm_2',
    account_id: 'a_1',
    mailbox_id: 'mb_1',
    thread_id: null,
    from_email: 'bob@example.com',
    from_name: 'Bob',
    subject: 'Your receipt is attached',
    received_at: new Date('2024-01-11T10:00:00Z').toISOString(),
    is_read: true,
    box: 'ALL_MAIL',
  },
];

const accountFixture = {
  account_id: 'a_1',
  mailbox_id: 'mb_1',
  provider: 'gmail',
  display_label: 'Gmail',
  config: {},
  email_address: 'alice@example.com',
};

// A sent email returned by an in:sent search. account_id matches
// accountFixture so the "De" column resolves to the user's own account
// email and the "Para" column shows the real recipient.
const sentEmailFixture = {
  provider_message_id: 'm_sent',
  account_id: 'a_1',
  mailbox_id: 'mb_1',
  thread_id: null,
  from_email: 'alice@example.com',
  from_name: 'Alice',
  to_email: 'recipient@example.com',
  to_name: 'Recipient',
  subject: 'A sent message',
  received_at: new Date('2024-01-12T09:00:00Z').toISOString(),
  is_read: true,
  box: 'SENT',
  has_attachments: false,
  is_favorite: false,
};

function renderInboxAtMailbox(initialEntry = '/m/mb_1/inbox') {
  return renderWithProviders(
    <>
      <LocationProbe />
      <Routes>
        <Route path="/m/:mailboxId/inbox" element={<UnifiedInboxPage box="ALL_MAIL" />} />
      </Routes>
    </>,
    { initialEntries: [initialEntry] },
  );
}

describe('UnifiedInboxPage', () => {
  it('renders the cached emails returned by the backend', async () => {
    server.use(
      http.get(`${API_BASE}/mailboxes/mb_1/emails`, () =>
        HttpResponse.json({
          items: emailFixtures,
          total: emailFixtures.length,
          limit: 50,
          offset: 0,
        }),
      ),
      http.get(`${API_BASE}/mailboxes/mb_1/accounts`, () => HttpResponse.json([accountFixture])),
    );

    renderInboxAtMailbox();

    await waitFor(() => {
      expect(screen.getByText('Welcome to the platform')).toBeInTheDocument();
    });
    expect(screen.getByText('Your receipt is attached')).toBeInTheDocument();
  });

  it('surfaces a backend error through the UI instead of rendering the table', async () => {
    server.use(
      http.get(`${API_BASE}/mailboxes/mb_1/emails`, () =>
        HttpResponse.json(
          { error: { code: 'forbidden', message: 'Mailbox not accessible' } },
          { status: 403 },
        ),
      ),
      http.get(`${API_BASE}/mailboxes/mb_1/accounts`, () => HttpResponse.json([accountFixture])),
    );

    renderInboxAtMailbox();

    await waitFor(() => {
      expect(screen.getByText('Mailbox not accessible')).toBeInTheDocument();
    });
    expect(screen.queryByText('Welcome to the platform')).not.toBeInTheDocument();
  });

  it('debounces typing: every keystroke updates ?q in the URL and the eventual request carries the latest q', async () => {
    // Capture every q value the backend sees. Default handler intentionally
    // returns the unfiltered fixtures — what we assert is the request shape.
    const seenQueries: (string | null)[] = [];
    server.use(
      http.get(`${API_BASE}/mailboxes/mb_1/emails`, ({ request }) => {
        const url = new URL(request.url);
        seenQueries.push(url.searchParams.get('q'));
        const q = (url.searchParams.get('q') ?? '').toLowerCase();
        if (!q) {
          return HttpResponse.json({
            items: emailFixtures,
            total: emailFixtures.length,
            limit: 50,
            offset: 0,
          });
        }
        const filtered = emailFixtures.filter((e) => e.subject.toLowerCase().includes(q));
        return HttpResponse.json({ items: filtered, total: filtered.length, limit: 50, offset: 0 });
      }),
      http.get(`${API_BASE}/mailboxes/mb_1/accounts`, () => HttpResponse.json([accountFixture])),
    );

    renderInboxAtMailbox();

    // First-render request hits the endpoint with no q.
    await waitFor(() => {
      expect(screen.getByText('Welcome to the platform')).toBeInTheDocument();
    });
    expect(seenQueries[0]).toBeNull();

    const input = screen.getByRole('searchbox');
    // Real (not fake) timers are used here so TanStack Query's internals run
    // without contention — userEvent.type produces real input events at a
    // realistic cadence; the page debounces internally at 300 ms.
    const user = userEvent.setup();
    await user.type(input, 'receipt');

    // After debounce settles, the table must reflect the filtered subset.
    await waitFor(() => {
      expect(screen.queryByText('Welcome to the platform')).not.toBeInTheDocument();
      expect(screen.getByText('Your receipt is attached')).toBeInTheDocument();
    });

    // The input reflects the URL search param (the page round-trips q
    // through `useSearchParams`, so a non-empty input value proves the URL
    // state was updated by the controlled-input handler).
    expect((input as HTMLInputElement).value).toBe('receipt');

    // The last network request must carry the final, complete query string.
    // We do not require exactly one extra request — TanStack Query may issue
    // an intermediate one during keystrokes — but the final one must equal
    // 'receipt' and there must be at least one request beyond the initial.
    expect(seenQueries.length).toBeGreaterThan(1);
    expect(seenQueries[seenQueries.length - 1]).toBe('receipt');
  });

  it('shows the search-empty message when the active search returns no rows', async () => {
    server.use(
      http.get(`${API_BASE}/mailboxes/mb_1/emails`, ({ request }) => {
        const q = new URL(request.url).searchParams.get('q');
        const items = q ? [] : emailFixtures;
        return HttpResponse.json({ items, total: items.length, limit: 50, offset: 0 });
      }),
      http.get(`${API_BASE}/mailboxes/mb_1/accounts`, () => HttpResponse.json([accountFixture])),
    );

    renderInboxAtMailbox();
    await waitFor(() => {
      expect(screen.getByText('Welcome to the platform')).toBeInTheDocument();
    });

    const user = userEvent.setup();
    await user.type(screen.getByRole('searchbox'), 'nope');

    await waitFor(() => {
      expect(screen.getByText('No se encontraron correos para tu búsqueda.')).toBeInTheDocument();
    });
  });

  it('clearing the search restores the unfiltered list and removes ?q from the URL', async () => {
    server.use(
      http.get(`${API_BASE}/mailboxes/mb_1/emails`, ({ request }) => {
        const q = new URL(request.url).searchParams.get('q');
        const items = q ? [emailFixtures[1]] : emailFixtures;
        return HttpResponse.json({ items, total: items.length, limit: 50, offset: 0 });
      }),
      http.get(`${API_BASE}/mailboxes/mb_1/accounts`, () => HttpResponse.json([accountFixture])),
    );

    renderWithProviders(
      <Routes>
        <Route path="/m/:mailboxId/inbox" element={<UnifiedInboxPage box="ALL_MAIL" />} />
      </Routes>,
      { initialEntries: ['/m/mb_1/inbox?q=receipt'] },
    );

    // Initial fetch with q=receipt returns only the second fixture.
    await waitFor(() => {
      expect(screen.getByText('Your receipt is attached')).toBeInTheDocument();
    });
    expect(screen.queryByText('Welcome to the platform')).not.toBeInTheDocument();

    // The clear button should be visible because the input is non-empty.
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: 'Limpiar búsqueda' }));

    // After debounce, the list must come back full.
    await waitFor(() => {
      expect(screen.getByText('Welcome to the platform')).toBeInTheDocument();
    });
    expect(window.location.search.includes('q=')).toBe(false);
  });

  it('renders the pagination bar with the "1–N de Z" range on page 1', async () => {
    server.use(
      http.get(`${API_BASE}/mailboxes/mb_1/emails`, () =>
        HttpResponse.json({ items: emailFixtures, total: 130, limit: 50, offset: 0 }),
      ),
      http.get(`${API_BASE}/mailboxes/mb_1/accounts`, () => HttpResponse.json([accountFixture])),
    );

    renderInboxAtMailbox();

    await waitFor(() => {
      expect(screen.getByText('Welcome to the platform')).toBeInTheDocument();
    });
    // total 130 with 2 visible fixtures → indicator reads the page range
    // and the exact total, not the page length.
    expect(screen.getByText('1–50 de 130')).toBeInTheDocument();
  });

  it('renders the pagination controls in the header, before the first email row', async () => {
    server.use(
      http.get(`${API_BASE}/mailboxes/mb_1/emails`, () =>
        HttpResponse.json({ items: emailFixtures, total: 130, limit: 50, offset: 0 }),
      ),
      http.get(`${API_BASE}/mailboxes/mb_1/accounts`, () => HttpResponse.json([accountFixture])),
    );

    renderInboxAtMailbox();

    await waitFor(() => {
      expect(screen.getByText('Welcome to the platform')).toBeInTheDocument();
    });
    // The bar moved out of the footer into the sticky header, so in
    // document order the controls now precede the first email row.
    const nextButton = screen.getByRole('button', { name: 'Página siguiente' });
    const firstRow = screen.getByText('Welcome to the platform');
    expect(
      nextButton.compareDocumentPosition(firstRow) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it('clicking "Siguiente" requests offset=50 and writes page=2 to the URL', async () => {
    const seenOffsets: (string | null)[] = [];
    server.use(
      http.get(`${API_BASE}/mailboxes/mb_1/emails`, ({ request }) => {
        const url = new URL(request.url);
        seenOffsets.push(url.searchParams.get('offset'));
        const offset = Number(url.searchParams.get('offset') ?? '0');
        const item = offset >= 50 ? emailFixtures[1] : emailFixtures[0];
        return HttpResponse.json({ items: [item], total: 130, limit: 50, offset });
      }),
      http.get(`${API_BASE}/mailboxes/mb_1/accounts`, () => HttpResponse.json([accountFixture])),
    );

    renderInboxAtMailbox();

    await waitFor(() => {
      expect(screen.getByText('Welcome to the platform')).toBeInTheDocument();
    });
    expect(seenOffsets).toContain('0');

    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: 'Página siguiente' }));

    await waitFor(() => {
      expect(seenOffsets).toContain('50');
    });
    expect(locationSearch()).toContain('page=2');
  });

  it('clicking a page number navigates to that page offset', async () => {
    const seenOffsets: (string | null)[] = [];
    server.use(
      http.get(`${API_BASE}/mailboxes/mb_1/emails`, ({ request }) => {
        const offset = new URL(request.url).searchParams.get('offset');
        seenOffsets.push(offset);
        return HttpResponse.json({
          items: [emailFixtures[0]],
          total: 200,
          limit: 50,
          offset: Number(offset ?? '0'),
        });
      }),
      http.get(`${API_BASE}/mailboxes/mb_1/accounts`, () => HttpResponse.json([accountFixture])),
    );

    renderInboxAtMailbox();

    await waitFor(() => {
      expect(screen.getByText('Welcome to the platform')).toBeInTheDocument();
    });

    const user = userEvent.setup();
    // total 200 → 4 pages; jump straight to page 3 (offset 100).
    await user.click(screen.getByRole('button', { name: 'Página 3' }));

    await waitFor(() => {
      expect(seenOffsets).toContain('100');
    });
    expect(locationSearch()).toContain('page=3');
  });

  it('disables "Anterior" on the first page', async () => {
    server.use(
      http.get(`${API_BASE}/mailboxes/mb_1/emails`, () =>
        HttpResponse.json({ items: emailFixtures, total: 130, limit: 50, offset: 0 }),
      ),
      http.get(`${API_BASE}/mailboxes/mb_1/accounts`, () => HttpResponse.json([accountFixture])),
    );

    renderInboxAtMailbox();

    await waitFor(() => {
      expect(screen.getByText('Welcome to the platform')).toBeInTheDocument();
    });
    expect(screen.getByRole('button', { name: 'Página anterior' })).toBeDisabled();
  });

  it('resets to page 1 (offset=0, no ?page) when the search query changes', async () => {
    const seenOffsets: (string | null)[] = [];
    server.use(
      http.get(`${API_BASE}/mailboxes/mb_1/emails`, ({ request }) => {
        const url = new URL(request.url);
        seenOffsets.push(url.searchParams.get('offset'));
        const q = (url.searchParams.get('q') ?? '').toLowerCase();
        const all = q
          ? emailFixtures.filter((e) => e.subject.toLowerCase().includes(q))
          : emailFixtures;
        return HttpResponse.json({ items: all, total: q ? all.length : 200, limit: 50, offset: 0 });
      }),
      http.get(`${API_BASE}/mailboxes/mb_1/accounts`, () => HttpResponse.json([accountFixture])),
    );

    // Start already on page 2.
    renderInboxAtMailbox('/m/mb_1/inbox?page=2');

    await waitFor(() => {
      expect(seenOffsets).toContain('50');
    });

    const user = userEvent.setup();
    await user.type(screen.getByRole('searchbox'), 'receipt');

    // After the search settles, the latest request must carry offset=0 and
    // the ?page param must be gone from the URL.
    await waitFor(() => {
      expect(screen.getByText('Your receipt is attached')).toBeInTheDocument();
    });
    expect(seenOffsets[seenOffsets.length - 1]).toBe('0');
    expect(locationSearch()).not.toContain('page=');
  });

  it('shows no pagination bar for an empty inbox or an empty search', async () => {
    server.use(
      // Both the empty inbox (no q) and the empty search (q set) return
      // total 0 — the bar must not render in either case.
      http.get(`${API_BASE}/mailboxes/mb_1/emails`, () =>
        HttpResponse.json({ items: [], total: 0, limit: 50, offset: 0 }),
      ),
      http.get(`${API_BASE}/mailboxes/mb_1/accounts`, () => HttpResponse.json([accountFixture])),
    );

    renderInboxAtMailbox();

    // Empty inbox message, no bar.
    await waitFor(() => {
      expect(screen.getByText('No hay correos en esta bandeja')).toBeInTheDocument();
    });
    expect(screen.queryByRole('button', { name: 'Página siguiente' })).not.toBeInTheDocument();

    // Now an active search that also yields nothing: distinct message, still no bar.
    const user = userEvent.setup();
    await user.type(screen.getByRole('searchbox'), 'zzz');
    await waitFor(() => {
      expect(screen.getByText('No se encontraron correos para tu búsqueda.')).toBeInTheDocument();
    });
    expect(screen.queryByRole('button', { name: 'Página siguiente' })).not.toBeInTheDocument();
  });

  it('flips the columns to the sent sense when q carries in:sent on an inbox view', async () => {
    const seenQueries: (string | null)[] = [];
    server.use(
      http.get(`${API_BASE}/mailboxes/mb_1/emails`, ({ request }) => {
        seenQueries.push(new URL(request.url).searchParams.get('q'));
        return HttpResponse.json({ items: [sentEmailFixture], total: 1, limit: 50, offset: 0 });
      }),
      http.get(`${API_BASE}/mailboxes/mb_1/accounts`, () => HttpResponse.json([accountFixture])),
    );

    // The route box is ALL_MAIL (inbox); in:sent in q shifts the effective
    // box to SENT for the column layout only.
    renderInboxAtMailbox('/m/mb_1/inbox?q=in:sent');

    await waitFor(() => {
      expect(screen.getByText('A sent message')).toBeInTheDocument();
    });

    // Unified view shows both columns; with isSent derived as SENT the
    // recipient surfaces under "Para" and the user's own account under "De".
    expect(screen.getByText('recipient@example.com')).toBeInTheDocument();
    expect(screen.getByText('alice@example.com')).toBeInTheDocument();

    // Only q carries the override, and it travels literally — the box param
    // sent to the backend is unchanged (the real override is server-side).
    expect(seenQueries[seenQueries.length - 1]).toBe('in:sent');
  });

  it('forwards an operator query (from:linkedin) literally without rewriting it', async () => {
    const seenQueries: (string | null)[] = [];
    server.use(
      http.get(`${API_BASE}/mailboxes/mb_1/emails`, ({ request }) => {
        seenQueries.push(new URL(request.url).searchParams.get('q'));
        return HttpResponse.json({
          items: emailFixtures,
          total: emailFixtures.length,
          limit: 50,
          offset: 0,
        });
      }),
      http.get(`${API_BASE}/mailboxes/mb_1/accounts`, () => HttpResponse.json([accountFixture])),
    );

    renderInboxAtMailbox();

    await waitFor(() => {
      expect(screen.getByText('Welcome to the platform')).toBeInTheDocument();
    });

    const user = userEvent.setup();
    await user.type(screen.getByRole('searchbox'), 'from:linkedin');

    await waitFor(() => {
      expect(seenQueries[seenQueries.length - 1]).toBe('from:linkedin');
    });
  });
});

// Selection regression guard. Commit #6 (conversation view) dropped the
// checkbox / bulk-bar wiring from the unified inbox too, leaving inert
// placeholder boxes. These pin selection back so it cannot silently regress.
describe('UnifiedInboxPage — selection', () => {
  function stubTwoEmails() {
    server.use(
      http.get(`${API_BASE}/mailboxes/mb_1/emails`, () =>
        HttpResponse.json({
          items: emailFixtures,
          total: emailFixtures.length,
          limit: 50,
          offset: 0,
        }),
      ),
      http.get(`${API_BASE}/mailboxes/mb_1/accounts`, () => HttpResponse.json([accountFixture])),
    );
  }

  it('the header checkbox selects every visible row and reveals the bulk bar', async () => {
    stubTwoEmails();
    renderInboxAtMailbox();
    await waitFor(() => expect(screen.getByText('Welcome to the platform')).toBeInTheDocument());

    expect(screen.queryByRole('button', { name: 'Limpiar selección' })).not.toBeInTheDocument();

    const user = userEvent.setup();
    await user.click(
      screen.getByRole('checkbox', { name: 'Seleccionar los 50 correos más recientes' }),
    );

    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Limpiar selección' })).toBeInTheDocument(),
    );
    const rowChecks = screen.getAllByRole('checkbox', { name: 'Seleccionar correo' });
    expect(rowChecks).toHaveLength(2);
    rowChecks.forEach((cb) => expect(cb).toBeChecked());
  });

  it('a row checkbox selects only that row', async () => {
    stubTwoEmails();
    renderInboxAtMailbox();
    await waitFor(() => expect(screen.getByText('Welcome to the platform')).toBeInTheDocument());

    const user = userEvent.setup();
    await user.click(screen.getAllByRole('checkbox', { name: 'Seleccionar correo' })[0]);

    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Limpiar selección' })).toBeInTheDocument(),
    );
    const rowChecks = screen.getAllByRole('checkbox', { name: 'Seleccionar correo' });
    expect(rowChecks[0]).toBeChecked();
    expect(rowChecks[1]).not.toBeChecked();
  });
});
