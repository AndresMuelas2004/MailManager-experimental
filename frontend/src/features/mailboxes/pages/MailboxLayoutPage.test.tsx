/**
 * Integration tests for MailboxLayoutPage (its MailboxShell subcomponent).
 *
 * Two feature surfaces this page owns are pinned here:
 *  - The responsive mobile shell: the hamburger / drawer / backdrop / floating
 *    compose button and the composer opening from the FAB.
 *  - The unread-count surfaces: the Sidebar badges next to "Bandeja unificada"
 *    (ALL_MAIL) and "Spam" (SPAM), and the browser tab title (``(N) MISSELA``).
 *
 * HTTP is intercepted at MSW; the real hooks (useMailboxList,
 * useMailboxUnreadCounts), endpoints, schema validation and React Query cache
 * all run. The unread-count handler is overridden per test to branch on the
 * ``box`` query param.
 *
 * ``document.title`` is global jsdom state, so it is reset after every test to
 * stop one spec leaking into the next.
 */

import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it } from 'vitest';

import { renderWithProviders } from '../../../test/renderWithProviders';
import { pinTestLang } from '../../../test/i18nTestLang';
import { server } from '../../../test/msw/server';
import DraftComposerProvider from '../../../app/providers/DraftComposerProvider';
import DraftComposerMount from '../../drafts/pages/DraftComposerMount';
import MailboxLayoutPage from './MailboxLayoutPage';

const API_BASE = 'http://localhost:8000';

// Pin Spanish so the fixed aria-label / title assertions hold (jsdom defaults
// to English). The mobile shell is pure CSS responsiveness — jsdom never
// evaluates the ``lg:`` media queries, so every assertion below is on
// behaviour (aria-* state, element presence, the composer opening) rather than
// on visibility, exactly as the responsive feature requires.
pinTestLang('es');

// ``document.title`` is global jsdom state written by the tab-title effect, so
// reset it after every test to stop one spec leaking into the next.
afterEach(() => {
  document.title = 'MISSELA';
});

// Translated aria-labels / copy the shell exposes (see locales/es.ts).
const HAMBURGER = 'Abrir menú'; // nav.openMenu
const CLOSE = 'Cerrar'; // common.close — drawer X (and, once open, the composer X)
const COMPOSE = 'Redactar'; // sidebar.compose — BOTH the FAB and the sidebar's round button
const INBOX_LINK = 'Bandeja unificada'; // nav.inbox — a drawer NavLink
const ARCHIVE_LINK = 'Archivados'; // nav.archive — the new "Archived" NavLink
const COMPOSER_TITLE = 'Nuevo mensaje'; // composer.titleNewEmail

const mailboxFixture = {
  mailbox_id: 'mb_1',
  display_name: 'Trabajo',
  owner_user_id: 'u_test',
  created_at: new Date('2024-01-01T00:00:00Z').toISOString(),
};

// GET /mailboxes must return the mounted mailbox so MailboxShell resolves
// ``currentMailboxName`` (the default handler returns []).
function stubMailboxes() {
  server.use(http.get(`${API_BASE}/mailboxes`, () => HttpResponse.json([mailboxFixture])));
}

// Mirror the real router nesting (router.tsx): the mailbox shell wraps the
// pathless DraftComposerMount (which mounts the singleton composer host next to
// the content Outlet), which wraps the content route. The content element is an
// inert stub — the composer overlay is rendered by the host, not by the page.
function renderShell(initialEntry = '/m/mb_1/inbox') {
  return renderWithProviders(
    <DraftComposerProvider>
      <Routes>
        <Route path="/m/:mailboxId" element={<MailboxLayoutPage />}>
          <Route element={<DraftComposerMount />}>
            <Route path="inbox" element={<div data-testid="inbox-content" />} />
            <Route path="settings" element={<div data-testid="settings-content" />} />
          </Route>
        </Route>
      </Routes>
    </DraftComposerProvider>,
    { initialEntries: [initialEntry] },
  );
}

// The drawer (Sidebar) and the FAB both carry aria-label ``Redactar``; the
// sidebar's button lives inside <aside id="mailbox-sidebar">, the FAB does not.
function getFab(): HTMLElement {
  const aside = document.getElementById('mailbox-sidebar') as HTMLElement;
  return screen.getAllByRole('button', { name: COMPOSE }).filter((btn) => !aside.contains(btn))[0];
}

describe('MailboxLayoutPage — mobile shell', () => {
  it('renders the hamburger collapsed and wired to the sidebar', () => {
    stubMailboxes();
    renderShell();

    const hamburger = screen.getByRole('button', { name: HAMBURGER });
    expect(hamburger).toHaveAttribute('aria-expanded', 'false');
    expect(hamburger).toHaveAttribute('aria-controls', 'mailbox-sidebar');
  });

  it('renders the floating compose button (FAB)', () => {
    stubMailboxes();
    renderShell();

    // Two ``Redactar`` buttons exist (sidebar + FAB); the FAB is the one
    // outside the aside.
    expect(getFab()).toBeInTheDocument();
  });

  it('renders the "Archivados" sidebar nav link pointing at the archive route', () => {
    stubMailboxes();
    renderShell();

    const link = screen.getByRole('link', { name: ARCHIVE_LINK });
    expect(link).toBeInTheDocument();
    // The NavLink resolves ``path: 'archive'`` relative to /m/:mailboxId.
    expect(link).toHaveAttribute('href', '/m/mb_1/archive');
  });

  it('opening the hamburger expands it and reveals the drawer close button', async () => {
    stubMailboxes();
    renderShell();
    const user = userEvent.setup();

    const hamburger = screen.getByRole('button', { name: HAMBURGER });
    await user.click(hamburger);

    expect(hamburger).toHaveAttribute('aria-expanded', 'true');
    // Before the composer is opened, the only ``Cerrar`` button is the drawer's.
    expect(screen.getByRole('button', { name: CLOSE })).toBeInTheDocument();
  });

  it('the drawer close button collapses the drawer', async () => {
    stubMailboxes();
    renderShell();
    const user = userEvent.setup();

    const hamburger = screen.getByRole('button', { name: HAMBURGER });
    await user.click(hamburger);
    await user.click(screen.getByRole('button', { name: CLOSE }));

    expect(hamburger).toHaveAttribute('aria-expanded', 'false');
  });

  it('clicking a drawer navigation link collapses the drawer', async () => {
    stubMailboxes();
    renderShell();
    const user = userEvent.setup();

    const hamburger = screen.getByRole('button', { name: HAMBURGER });
    await user.click(hamburger);
    expect(hamburger).toHaveAttribute('aria-expanded', 'true');

    // The sidebar NavLink fires ``onNavigate`` (closeDrawer) on click.
    await user.click(screen.getByRole('link', { name: INBOX_LINK }));

    expect(hamburger).toHaveAttribute('aria-expanded', 'false');
  });

  it('tapping the backdrop collapses the drawer', async () => {
    stubMailboxes();
    renderShell();
    const user = userEvent.setup();

    const hamburger = screen.getByRole('button', { name: HAMBURGER });
    await user.click(hamburger);
    // The dimmed backdrop only mounts while the drawer is open and closes it on
    // click. It is aria-hidden (no accessible role), so reach it by its
    // structural fingerprint: the full-screen fixed overlay that mounts
    // alongside the open drawer.
    const backdrop = document.querySelector(
      'div[aria-hidden="true"].fixed.inset-0',
    ) as HTMLElement | null;
    expect(backdrop).not.toBeNull();
    await user.click(backdrop as HTMLElement);

    expect(hamburger).toHaveAttribute('aria-expanded', 'false');
  });

  it('pressing the FAB opens the composer', async () => {
    stubMailboxes();
    renderShell();
    const user = userEvent.setup();

    expect(screen.queryByText(COMPOSER_TITLE)).not.toBeInTheDocument();

    await user.click(getFab());

    // The composer host renders ComposeOverlay once ``openForNewEmail`` flips
    // the composer open; its header shows the new-message title.
    await waitFor(() => {
      expect(screen.getByText(COMPOSER_TITLE)).toBeInTheDocument();
    });
  });
});

// Override the unread-count endpoint to return distinct figures per box.
function useUnreadCounts(mailboxId: string, inboxTotal: number, spamTotal: number) {
  server.use(
    http.get(`${API_BASE}/mailboxes/:mailboxId/emails/unread-count`, ({ params, request }) => {
      const box = new URL(request.url).searchParams.get('box') ?? 'ALL_MAIL';
      return HttpResponse.json({
        mailbox_id: String(params.mailboxId),
        box,
        total: box === 'SPAM' ? spamTotal : inboxTotal,
        accounts: [{ account_id: 'a_1', unread: box === 'SPAM' ? spamTotal : inboxTotal }],
      });
    }),
  );
  return mailboxId;
}

function renderLayout() {
  return renderWithProviders(
    <Routes>
      <Route path="/m/:mailboxId" element={<MailboxLayoutPage />}>
        <Route path="inbox" element={<div>Inbox content</div>} />
      </Route>
    </Routes>,
    { initialEntries: ['/m/mb_1/inbox'] },
  );
}

describe('MailboxLayoutPage — unread badges and tab title', () => {
  it('shows the Sidebar badges for the inbox and spam totals', async () => {
    useUnreadCounts('mb_1', 5, 2);
    renderLayout();

    // The Inbox nav entry carries the ALL_MAIL total, Spam the SPAM total.
    await waitFor(() => expect(screen.getByLabelText('5 sin leer')).toBeInTheDocument());
    expect(screen.getByLabelText('2 sin leer')).toBeInTheDocument();
    // The badge text mirrors the count.
    expect(screen.getByLabelText('5 sin leer')).toHaveTextContent('5');
  });

  it('sets the browser tab title from the inbox total', async () => {
    useUnreadCounts('mb_1', 5, 2);
    renderLayout();

    await waitFor(() => expect(document.title).toBe('(5) MISSELA'));
  });

  it('renders no badge and a plain title when both totals are zero', async () => {
    useUnreadCounts('mb_1', 0, 0);
    renderLayout();

    // The inbox child renders, so the shell has mounted and the queries ran.
    await waitFor(() => expect(screen.getByText('Inbox content')).toBeInTheDocument());
    // Badge hides itself at 0 → no "… sin leer" labelled element anywhere.
    expect(screen.queryByLabelText(/sin leer/)).not.toBeInTheDocument();
    expect(document.title).toBe('MISSELA');
  });
});

const accountFixture = {
  account_id: 'a_1',
  mailbox_id: 'mb_1',
  provider: 'gmail',
  display_label: 'Gmail',
  config: {},
  email_address: 'one@example.com',
  signature_html: null,
};

// Mount the folder routes for both scopes so the switcher's dynamic-base links
// resolve and useMatch can detect the account scope from the URL.
function renderScope(initialEntry: string) {
  return renderWithProviders(
    <Routes>
      <Route path="/m/:mailboxId" element={<MailboxLayoutPage />}>
        <Route path="inbox" element={<div>Unified inbox</div>} />
        <Route path="archive" element={<div>Unified archive</div>} />
        <Route path="virtual-mailboxes" element={<div>Virtual mailboxes</div>} />
        <Route path="settings/accounts" element={<div>Accounts settings</div>} />
        <Route path="account/:accountId">
          <Route path="inbox" element={<div>Account inbox</div>} />
          <Route path="archive" element={<div>Account archive</div>} />
        </Route>
      </Route>
    </Routes>,
    { initialEntries: [initialEntry] },
  );
}

describe('MailboxLayoutPage — sidebar scope switcher', () => {
  it('keeps the account list collapsed and reveals it from the trigger', async () => {
    stubMailboxes();
    server.use(
      http.get(`${API_BASE}/mailboxes/mb_1/accounts`, () => HttpResponse.json([accountFixture])),
    );

    renderScope('/m/mb_1/inbox');
    const user = userEvent.setup();

    // Collapsed by default: the trigger shows the unified scope; accounts hidden.
    const trigger = await screen.findByRole('button', { name: /Todas las cuentas/ });
    expect(screen.queryByText('one@example.com')).not.toBeInTheDocument();

    // Expanding the trigger reveals each account.
    await user.click(trigger);
    expect(screen.getByText('one@example.com')).toBeInTheDocument();
  });

  it('points folder links at the account base inside an account scope, keeping Bandejas ficticias global', async () => {
    stubMailboxes();
    server.use(
      http.get(`${API_BASE}/mailboxes/mb_1/accounts`, () => HttpResponse.json([accountFixture])),
    );

    renderScope('/m/mb_1/account/a_1/inbox');
    await waitFor(() => expect(screen.getByText('one@example.com')).toBeInTheDocument());

    // Folder links now target this account's routes (the inbox entry also swaps
    // its label to "Bandeja de entrada" outside the unified scope).
    expect(screen.getByRole('link', { name: 'Bandeja de entrada' })).toHaveAttribute(
      'href',
      '/m/mb_1/account/a_1/inbox',
    );
    expect(screen.getByRole('link', { name: 'Archivados' })).toHaveAttribute(
      'href',
      '/m/mb_1/account/a_1/archive',
    );
    // Bandejas ficticias has no per-account route → it always stays on the unified base.
    expect(screen.getByRole('link', { name: 'Bandejas ficticias' })).toHaveAttribute(
      'href',
      '/m/mb_1/virtual-mailboxes',
    );
  });

  it('shows the active account unread total on the inbox badge, not the mailbox-wide total', async () => {
    stubMailboxes();
    server.use(
      http.get(`${API_BASE}/mailboxes/mb_1/accounts`, () => HttpResponse.json([accountFixture])),
      http.get(`${API_BASE}/mailboxes/:mailboxId/emails/unread-count`, ({ params, request }) => {
        const box = new URL(request.url).searchParams.get('box') ?? 'ALL_MAIL';
        // Mailbox-wide ALL_MAIL total is 8, but this account (a_1) has 7.
        return HttpResponse.json({
          mailbox_id: String(params.mailboxId),
          box,
          total: box === 'SPAM' ? 0 : 8,
          accounts: [{ account_id: 'a_1', unread: box === 'SPAM' ? 0 : 7 }],
        });
      }),
    );

    renderScope('/m/mb_1/account/a_1/inbox');

    // In the a_1 scope the inbox badge shows a_1's unread (7), not the total (8).
    await waitFor(() => expect(screen.getByLabelText('7 sin leer')).toBeInTheDocument());
    expect(screen.queryByLabelText('8 sin leer')).not.toBeInTheDocument();
  });

  it('the switcher offers "Añadir cuenta" and it navigates to the connected-accounts settings', async () => {
    stubMailboxes();
    server.use(
      http.get(`${API_BASE}/mailboxes/mb_1/accounts`, () => HttpResponse.json([accountFixture])),
    );

    renderScope('/m/mb_1/inbox');
    const user = userEvent.setup();

    await user.click(await screen.findByRole('button', { name: /Todas las cuentas/ }));
    await user.click(screen.getByRole('button', { name: 'Añadir cuenta' }));

    await waitFor(() => expect(screen.getByText('Accounts settings')).toBeInTheDocument());
  });

  it('the "Añadir cuenta" entry is also offered inside a single-account scope', async () => {
    // The entry is permanent (not gated to the unified scope): a user viewing
    // one account can add the next one from the same dropdown.
    stubMailboxes();
    server.use(
      http.get(`${API_BASE}/mailboxes/mb_1/accounts`, () => HttpResponse.json([accountFixture])),
    );

    renderScope('/m/mb_1/account/a_1/inbox');
    const user = userEvent.setup();

    await user.click(await screen.findByRole('button', { name: /one@example.com/ }));
    expect(screen.getByRole('button', { name: 'Añadir cuenta' })).toBeInTheDocument();
  });
});
