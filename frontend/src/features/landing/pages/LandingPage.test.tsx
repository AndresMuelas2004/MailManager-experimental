import { QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it } from 'vitest';

import AuthProvider from '../../../app/providers/AuthProvider';
import RequireAuth from '../../../app/routes/RequireAuth';
import { I18nProvider, LANG_STORAGE_KEY } from '../../../lib/i18n';
import { server } from '../../../test/msw/server';
import { createTestQueryClient } from '../../../test/renderWithProviders';
import LandingPage from './LandingPage';

const API_BASE = 'http://localhost:8000';

const HERO_EN = 'All your email. One inbox.';
const HERO_ES = 'Todo tu correo. Una sola bandeja.';

// The default MSW /auth/me handler answers 200 with a user (authenticated).
// Anonymous scenarios override it per test with this 401.
function forceAnonymous() {
  server.use(
    http.get(`${API_BASE}/auth/me`, () =>
      HttpResponse.json(
        { error: { code: 'unauthorized', message: 'Not authenticated' } },
        { status: 401 },
      ),
    ),
  );
}

// Mirrors the public slice of the real route tree: "/" is the public landing
// (the page itself forwards authenticated visitors to /home), the gateway
// lives at /home behind the real RequireAuth, and /login is public. The real
// AuthProvider bootstraps through GET /auth/me so the session state comes from
// the network boundary, exactly as in production.
function renderLanding() {
  return render(
    <QueryClientProvider client={createTestQueryClient()}>
      <AuthProvider>
        <I18nProvider>
          <MemoryRouter initialEntries={['/']}>
            <Routes>
              <Route path="/login" element={<div>Login screen</div>} />
              <Route path="/">
                <Route index element={<LandingPage />} />
                <Route element={<RequireAuth />}>
                  <Route path="home" element={<div>Gateway screen</div>} />
                </Route>
              </Route>
            </Routes>
          </MemoryRouter>
        </I18nProvider>
      </AuthProvider>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  // Tests that seed or toggle the language must not leak it into other files.
  window.localStorage.removeItem(LANG_STORAGE_KEY);
});

describe('LandingPage', () => {
  it('shows the landing to an anonymous visitor at "/" without redirecting to /login', async () => {
    forceAnonymous();
    renderLanding();

    // jsdom's navigator.language is en-US → the public pages default to English.
    expect(await screen.findByRole('heading', { name: HERO_EN })).toBeInTheDocument();
    expect(screen.queryByText('Login screen')).not.toBeInTheDocument();
    expect(screen.queryByText('Gateway screen')).not.toBeInTheDocument();
  });

  it('forwards an authenticated visitor at "/" to the mailbox gateway', async () => {
    renderLanding();

    await waitFor(() => expect(screen.getByText('Gateway screen')).toBeInTheDocument());
    expect(screen.queryByRole('heading', { name: HERO_EN })).not.toBeInTheDocument();
  });

  it('navigates to /login through the hero CTA', async () => {
    forceAnonymous();
    renderLanding();

    // "Get started" appears twice (hero + final CTA); both lead to /login.
    const [heroCta] = await screen.findAllByRole('link', { name: 'Get started' });
    await userEvent.click(heroCta);

    await waitFor(() => expect(screen.getByText('Login screen')).toBeInTheDocument());
  });

  it('renders in Spanish when a stored language choice says so', async () => {
    forceAnonymous();
    window.localStorage.setItem(LANG_STORAGE_KEY, 'es');
    renderLanding();

    expect(await screen.findByRole('heading', { name: HERO_ES })).toBeInTheDocument();
  });

  it('switches language with the toggle and persists the choice', async () => {
    forceAnonymous();
    renderLanding();

    await screen.findByRole('heading', { name: HERO_EN });
    await userEvent.click(screen.getByRole('button', { name: 'ES' }));

    expect(await screen.findByRole('heading', { name: HERO_ES })).toBeInTheDocument();
    expect(window.localStorage.getItem(LANG_STORAGE_KEY)).toBe('es');
  });
});
