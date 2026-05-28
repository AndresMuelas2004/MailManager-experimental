import { QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { describe, expect, it } from 'vitest';

import AuthProvider from '../../../app/providers/AuthProvider';
import { server } from '../../../test/msw/server';
import { createTestQueryClient } from '../../../test/renderWithProviders';
import LoginPage from './LoginPage';

const API_BASE = 'http://localhost:8000';

function renderLoginAtRoute() {
  return render(
    <QueryClientProvider client={createTestQueryClient()}>
      <AuthProvider>
        <MemoryRouter initialEntries={['/login']}>
          <Routes>
            <Route path="/login" element={<LoginPage />} />
            <Route path="/" element={<div>Inbox landing</div>} />
          </Routes>
        </MemoryRouter>
      </AuthProvider>
    </QueryClientProvider>,
  );
}

describe('LoginPage — dev login backdoor', () => {
  it('renders the Dev login button under import.meta.env.DEV and authenticates on click', async () => {
    // AuthProvider boots by calling GET /auth/me; force unauthenticated so the
    // login surface renders (default MSW handler returns 200 → would Navigate away).
    server.use(
      http.get(`${API_BASE}/auth/me`, () =>
        HttpResponse.json(
          { error: { code: 'unauthorized', message: 'Not authenticated' } },
          { status: 401 },
        ),
      ),
    );

    renderLoginAtRoute();

    const button = await screen.findByRole('button', { name: /dev login/i });
    expect(button).toBeInTheDocument();

    await userEvent.click(button);

    // Default /auth/dev-login MSW handler resolves with the dev user; the
    // AuthProvider sets user, LoginPage redirects to "/".
    await waitFor(() => {
      expect(screen.getByText('Inbox landing')).toBeInTheDocument();
    });
  });

  it('surfaces a backend error through the button error slot', async () => {
    server.use(
      http.get(`${API_BASE}/auth/me`, () =>
        HttpResponse.json(
          { error: { code: 'unauthorized', message: 'Not authenticated' } },
          { status: 401 },
        ),
      ),
      http.post(`${API_BASE}/auth/dev-login`, () =>
        HttpResponse.json(
          {
            error: { code: 'dev_login_disabled', message: 'Dev login is disabled in this deploy.' },
          },
          { status: 503 },
        ),
      ),
    );

    renderLoginAtRoute();

    const button = await screen.findByRole('button', { name: /dev login/i });
    await userEvent.click(button);

    await waitFor(() => {
      expect(screen.getByText('Dev login is disabled in this deploy.')).toBeInTheDocument();
    });
  });
});
