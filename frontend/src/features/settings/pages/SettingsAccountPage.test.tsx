/**
 * Integration test for the "your account" settings section. It renders the
 * real AuthProvider (so logout / delete hit the MSW boundary), wrapped in the
 * real I18nProvider. The language is pinned to Spanish for deterministic
 * labels. The delete action is guarded: the confirm button stays disabled
 * until the user types their own email exactly.
 */

import { QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { MemoryRouter, Navigate, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import AuthProvider from '../../../app/providers/AuthProvider';
import { useAuth } from '../../../app/providers/AuthContext';
import RequireAuth from '../../../app/routes/RequireAuth';
import { I18nProvider } from '../../../lib/i18n';
import { server } from '../../../test/msw/server';
import { createTestQueryClient } from '../../../test/renderWithProviders';
import SettingsAccountPage from './SettingsAccountPage';

const API_BASE = 'http://localhost:8000';
const USER = { user_id: 'u_1', email: 'me@example.com', name: 'Me', avatar_url: null };

// Faithful stand-in for the real LoginPage: an authenticated visitor to /login
// is bounced back to "/". This is the guard that turns a logout which leaves
// `user` non-null into the create-mailbox leak, so the fixture must reproduce
// it for the regression tests to bite against the pre-fix behaviour.
function LoginStub() {
  const { user } = useAuth();
  return user ? <Navigate to="/" replace /> : <div>Login screen</div>;
}

// Mirrors the real route tree end to end: RequireAuth wraps the whole
// authenticated area; "/" (the index gateway) bounces to /create-mailbox when
// there are no mailboxes (what MailboxGatewayPage does); /login bounces back to
// "/" while authenticated (what LoginPage does). Logging out must redirect to
// /login through the guard with `user` cleared — never leak through "/" into
// create-mailbox, which is the regression this fixture exists to catch.
function renderAccountPage() {
  return render(
    <QueryClientProvider client={createTestQueryClient()}>
      <AuthProvider>
        <I18nProvider>
          <MemoryRouter initialEntries={['/m/mb_1/settings']}>
            <Routes>
              <Route path="/login" element={<LoginStub />} />
              <Route element={<RequireAuth />}>
                <Route index element={<Navigate to="/create-mailbox" replace />} />
                <Route path="/create-mailbox" element={<div>Create mailbox screen</div>} />
                <Route path="/m/:mailboxId/settings" element={<SettingsAccountPage />} />
              </Route>
            </Routes>
          </MemoryRouter>
        </I18nProvider>
      </AuthProvider>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  window.localStorage.setItem('lang', 'es');
  server.use(http.get(`${API_BASE}/auth/me`, () => HttpResponse.json(USER)));
});

afterEach(() => {
  window.localStorage.clear();
});

describe('SettingsAccountPage', () => {
  it('renders the signed-in identity once auth resolves', async () => {
    renderAccountPage();
    await waitFor(() => expect(screen.getByText('me@example.com')).toBeInTheDocument());
    expect(screen.getByRole('heading', { name: 'Tu cuenta' })).toBeInTheDocument();
  });

  it('logs out and lands on /login', async () => {
    renderAccountPage();
    await waitFor(() => expect(screen.getByText('me@example.com')).toBeInTheDocument());

    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: 'Cerrar sesión' }));

    await waitFor(() => expect(screen.getByText('Login screen')).toBeInTheDocument());
    // The redirect must go straight to /login via RequireAuth, never leak
    // through "/" into the create-mailbox gateway (the reported bug).
    expect(screen.queryByText('Create mailbox screen')).not.toBeInTheDocument();
  });

  it('still lands on /login when the server logout call fails', async () => {
    // Logout must clear the local session unconditionally: even if the server
    // call errors, the client must not stay "authenticated" (which would keep
    // RequireAuth mounted and strand the user inside the app).
    server.use(http.post(`${API_BASE}/auth/logout`, () => new HttpResponse(null, { status: 500 })));

    renderAccountPage();
    await waitFor(() => expect(screen.getByText('me@example.com')).toBeInTheDocument());

    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: 'Cerrar sesión' }));

    await waitFor(() => expect(screen.getByText('Login screen')).toBeInTheDocument());
    expect(screen.queryByText('Create mailbox screen')).not.toBeInTheDocument();
  });

  it('keeps the delete button disabled until the exact email is typed', async () => {
    renderAccountPage();
    await waitFor(() => expect(screen.getByText('me@example.com')).toBeInTheDocument());

    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: 'Eliminar cuenta' }));

    const confirmButton = screen.getByRole('button', { name: 'Eliminar mi cuenta' });
    expect(confirmButton).toBeDisabled();

    const input = screen.getByPlaceholderText('Escribe tu email');
    await user.type(input, 'wrong@example.com');
    expect(confirmButton).toBeDisabled();

    await user.clear(input);
    await user.type(input, 'me@example.com');
    expect(confirmButton).toBeEnabled();
  });

  it('deletes the account through DELETE /auth/me and returns to /login', async () => {
    let deleted = false;
    server.use(
      http.delete(`${API_BASE}/auth/me`, () => {
        deleted = true;
        return HttpResponse.json({ message: 'Deleted' });
      }),
    );

    renderAccountPage();
    await waitFor(() => expect(screen.getByText('me@example.com')).toBeInTheDocument());

    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: 'Eliminar cuenta' }));
    await user.type(screen.getByPlaceholderText('Escribe tu email'), 'me@example.com');
    await user.click(screen.getByRole('button', { name: 'Eliminar mi cuenta' }));

    await waitFor(() => expect(deleted).toBe(true));
    await waitFor(() => expect(screen.getByText('Login screen')).toBeInTheDocument());
    expect(screen.queryByText('Create mailbox screen')).not.toBeInTheDocument();
  });
});
