/**
 * Integration tests for the mailboxes settings page (rename / delete a
 * mailbox). HTTP is intercepted at the MSW boundary; the real hooks, endpoint
 * functions and query cache run. The UI language is pinned to Spanish so the
 * ``t()``-driven labels assert deterministically (the I18nProvider defaults to
 * the browser language — English in jsdom — unless ``localStorage['lang']`` is
 * seeded; see the i18n trap in the frontend tests guide).
 */

import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { Route, Routes, useLocation } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { renderWithProviders } from '../../../test/renderWithProviders';
import { server } from '../../../test/msw/server';
import MailboxesSettingsPage from './MailboxesSettingsPage';

const API_BASE = 'http://localhost:8000';

function LocationProbe() {
  const location = useLocation();
  return <span data-testid="location-pathname">{location.pathname}</span>;
}

function pathname(): string {
  return screen.getByTestId('location-pathname').textContent ?? '';
}

function mailbox(id: string, displayName: string) {
  return {
    mailbox_id: id,
    display_name: displayName,
    owner_user_id: 'u_test',
    created_at: new Date('2024-01-01T00:00:00Z').toISOString(),
  };
}

function renderPageAt(mailboxId: string) {
  return renderWithProviders(
    <>
      <LocationProbe />
      <Routes>
        <Route path="/m/:mailboxId/settings/mailboxes" element={<MailboxesSettingsPage />} />
        <Route path="/m/:mailboxId/inbox" element={<span>inbox stub</span>} />
        <Route path="/" element={<span>index stub</span>} />
      </Routes>
    </>,
    { initialEntries: [`/m/${mailboxId}/settings/mailboxes`] },
  );
}

beforeEach(() => {
  window.localStorage.setItem('lang', 'es');
});

afterEach(() => {
  window.localStorage.clear();
});

describe('MailboxesSettingsPage', () => {
  it('lists every mailbox returned by the backend', async () => {
    server.use(
      http.get(`${API_BASE}/mailboxes`, () =>
        HttpResponse.json([mailbox('mb_1', 'Personal'), mailbox('mb_2', 'Work')]),
      ),
    );

    renderPageAt('mb_1');

    await waitFor(() => expect(screen.getByText('Personal')).toBeInTheDocument());
    expect(screen.getByText('Work')).toBeInTheDocument();
  });

  it('shows the empty state when the user has no mailboxes', async () => {
    server.use(http.get(`${API_BASE}/mailboxes`, () => HttpResponse.json([])));

    renderPageAt('mb_1');

    await waitFor(() =>
      expect(screen.getByText('No tienes ninguna bandeja todavía.')).toBeInTheDocument(),
    );
  });

  it('renames a mailbox: PATCH carries the new display_name and the list refreshes', async () => {
    let patchedBody: Record<string, unknown> | null = null;
    let currentName = 'Personal';
    server.use(
      http.get(`${API_BASE}/mailboxes`, () => HttpResponse.json([mailbox('mb_1', currentName)])),
      http.patch(`${API_BASE}/mailboxes/:mailboxId`, async ({ params, request }) => {
        patchedBody = (await request.json()) as Record<string, unknown>;
        currentName = String(patchedBody.display_name);
        return HttpResponse.json(mailbox(String(params.mailboxId), currentName));
      }),
    );

    renderPageAt('mb_1');
    await waitFor(() => expect(screen.getByText('Personal')).toBeInTheDocument());

    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: 'Renombrar bandeja' }));

    const input = screen.getByPlaceholderText('Nombre de la bandeja');
    await user.clear(input);
    await user.type(input, 'Renamed Box');
    await user.click(screen.getByRole('button', { name: 'Guardar' }));

    await waitFor(() => expect(patchedBody).toEqual({ display_name: 'Renamed Box' }));
    // The mutation invalidates ['mailboxes'], so the refetched list shows the
    // new name (the GET handler now returns the renamed mailbox).
    await waitFor(() => expect(screen.getByText('Renamed Box')).toBeInTheDocument());
  });

  it('deletes the current mailbox and navigates to a surviving mailbox inbox', async () => {
    let deletedId: string | null = null;
    server.use(
      http.get(`${API_BASE}/mailboxes`, () =>
        HttpResponse.json([mailbox('mb_1', 'Personal'), mailbox('mb_2', 'Work')]),
      ),
      http.delete(`${API_BASE}/mailboxes/:mailboxId`, ({ params }) => {
        deletedId = String(params.mailboxId);
        return HttpResponse.json({ status: 'deleted' });
      }),
    );

    renderPageAt('mb_1');
    await waitFor(() => expect(screen.getByText('Personal')).toBeInTheDocument());

    const user = userEvent.setup();
    // The first delete button belongs to the first row (mb_1, the URL mailbox).
    await user.click(screen.getAllByRole('button', { name: 'Eliminar bandeja' })[0]);

    // Confirmation modal must appear before the request fires.
    expect(screen.getByText('¿Eliminar esta bandeja?')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Eliminar' }));

    await waitFor(() => expect(deletedId).toBe('mb_1'));
    // Deleting the URL mailbox routes the user to the surviving mailbox inbox.
    await waitFor(() => expect(pathname()).toBe('/m/mb_2/inbox'));
  });

  it('cancelling the delete confirmation fires no request', async () => {
    let deleteCalled = false;
    server.use(
      http.get(`${API_BASE}/mailboxes`, () => HttpResponse.json([mailbox('mb_1', 'Personal')])),
      http.delete(`${API_BASE}/mailboxes/:mailboxId`, () => {
        deleteCalled = true;
        return HttpResponse.json({ status: 'deleted' });
      }),
    );

    renderPageAt('mb_1');
    await waitFor(() => expect(screen.getByText('Personal')).toBeInTheDocument());

    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: 'Eliminar bandeja' }));
    await user.click(screen.getByRole('button', { name: 'Cancelar' }));

    expect(screen.queryByText('¿Eliminar esta bandeja?')).not.toBeInTheDocument();
    expect(deleteCalled).toBe(false);
    expect(pathname()).toBe('/m/mb_1/settings/mailboxes');
  });
});
