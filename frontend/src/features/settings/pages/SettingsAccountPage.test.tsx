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
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import AuthProvider from '../../../app/providers/AuthProvider';
import { I18nProvider } from '../../../lib/i18n';
import { server } from '../../../test/msw/server';
import { createTestQueryClient } from '../../../test/renderWithProviders';
import SettingsAccountPage from './SettingsAccountPage';

const API_BASE = 'http://localhost:8000';
const USER = { user_id: 'u_1', email: 'me@example.com', name: 'Me', avatar_url: null };

function renderAccountPage() {
  return render(
    <QueryClientProvider client={createTestQueryClient()}>
      <AuthProvider>
        <I18nProvider>
          <MemoryRouter initialEntries={['/m/mb_1/settings']}>
            <Routes>
              <Route path="/m/:mailboxId/settings" element={<SettingsAccountPage />} />
              <Route path="/login" element={<div>Login screen</div>} />
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
  });
});
