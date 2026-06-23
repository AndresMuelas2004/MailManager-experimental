/**
 * Integration tests for MailboxLayoutPage (its MailboxShell subcomponent).
 *
 * Two feature surfaces this page owns are pinned here:
 *  - The responsive mobile shell: the hamburger / drawer / backdrop / floating
 *    compose button and the composer opening from the FAB.
 *  - The unread-count surfaces: the Sidebar badges next to "Bandeja unificada"
 *    (ALL_MAIL) and "Spam" (SPAM), and the browser tab title (``(N) MailManager``).
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
  document.title = 'MailManager';
});

// Translated aria-labels / copy the shell exposes (see locales/es.ts).
const HAMBURGER = 'Abrir menú'; // nav.openMenu
const CLOSE = 'Cerrar'; // common.close — drawer X (and, once open, the composer X)
const COMPOSE = 'Redactar'; // sidebar.compose — BOTH the FAB and the sidebar's round button
const INBOX_LINK = 'Bandeja unificada'; // nav.inbox — a drawer NavLink
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

    await waitFor(() => expect(document.title).toBe('(5) MailManager'));
  });

  it('renders no badge and a plain title when both totals are zero', async () => {
    useUnreadCounts('mb_1', 0, 0);
    renderLayout();

    // The inbox child renders, so the shell has mounted and the queries ran.
    await waitFor(() => expect(screen.getByText('Inbox content')).toBeInTheDocument());
    // Badge hides itself at 0 → no "… sin leer" labelled element anywhere.
    expect(screen.queryByLabelText(/sin leer/)).not.toBeInTheDocument();
    expect(document.title).toBe('MailManager');
  });
});
