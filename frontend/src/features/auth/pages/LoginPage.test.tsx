import { QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import AuthProvider from '../../../app/providers/AuthProvider';
import { I18nProvider } from '../../../lib/i18n';
import { server } from '../../../test/msw/server';
import { createTestQueryClient } from '../../../test/renderWithProviders';
import { __resetMsalSingletonForTest } from '../hooks/useMicrosoftLogin';
import LoginPage from './LoginPage';

const API_BASE = 'http://localhost:8000';

// @azure/msal-browser cannot run loginPopup in jsdom (popup + COOP +
// BroadcastChannel), exactly like the Google GSI script. Mocking the
// third-party SDK is the sanctioned exception (test/CLAUDE.md §4 forbids
// mocking OUR code or the endpoint — MSW still intercepts POST /auth/microsoft).
// `vi.hoisted` holds the per-test loginPopup behaviour: the factory is hoisted
// above the imports, so it cannot close over a normal top-level variable.
const msalMocks = vi.hoisted(() => ({
  loginPopup: vi.fn(),
  initialize: vi.fn(() => Promise.resolve()),
}));

vi.mock('@azure/msal-browser', () => ({
  PublicClientApplication: class {
    initialize() {
      return msalMocks.initialize();
    }
    loginPopup(request: unknown) {
      return msalMocks.loginPopup(request);
    }
  },
}));

function renderLoginAtRoute() {
  // LoginPage and its branding/buttons read copy through ``t()``; the default
  // (English) locale is fine here — the assertions use English / a
  // case-insensitive matcher / backend-supplied text — but the provider must
  // still wrap the tree.
  return render(
    <QueryClientProvider client={createTestQueryClient()}>
      <AuthProvider>
        <I18nProvider>
          <MemoryRouter initialEntries={['/login']}>
            <Routes>
              <Route path="/login" element={<LoginPage />} />
              <Route path="/" element={<div>Inbox landing</div>} />
            </Routes>
          </MemoryRouter>
        </I18nProvider>
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

describe('LoginPage — Microsoft login', () => {
  // AuthProvider boots via GET /auth/me; force unauthenticated so the login
  // surface (both provider buttons) renders instead of redirecting to "/".
  beforeEach(() => {
    // Reset the lazy MSAL module singleton so each case starts clean — without
    // this the cached instance survives across tests in the worker and the
    // "client id absent" case becomes order-dependent (it would see a built
    // instance instead of null).
    __resetMsalSingletonForTest();
    server.use(
      http.get(`${API_BASE}/auth/me`, () =>
        HttpResponse.json(
          { error: { code: 'unauthorized', message: 'Not authenticated' } },
          { status: 401 },
        ),
      ),
    );
    msalMocks.loginPopup.mockReset();
    msalMocks.initialize.mockReset().mockResolvedValue(undefined);
  });

  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it('shows the not-configured message when VITE_MICROSOFT_CLIENT_ID is absent', async () => {
    vi.stubEnv('VITE_MICROSOFT_CLIENT_ID', '');

    renderLoginAtRoute();

    const button = await screen.findByRole('button', { name: /continue with microsoft/i });
    await userEvent.click(button);

    await waitFor(() => {
      expect(screen.getByText('Microsoft Client ID is not configured.')).toBeInTheDocument();
    });
    // The guard short-circuits before any SDK call.
    expect(msalMocks.loginPopup).not.toHaveBeenCalled();
  });

  it('authenticates on click and redirects to the inbox', async () => {
    vi.stubEnv('VITE_MICROSOFT_CLIENT_ID', 'test-cid');
    msalMocks.loginPopup.mockResolvedValue({ idToken: 'fake.jwt' });

    renderLoginAtRoute();

    const button = await screen.findByRole('button', { name: /continue with microsoft/i });
    await userEvent.click(button);

    // loginPopup → POST /auth/microsoft (default MSW handler) → setUser →
    // Navigate to "/".
    await waitFor(() => {
      expect(screen.getByText('Inbox landing')).toBeInTheDocument();
    });
  });

  it('surfaces a backend error under the button', async () => {
    vi.stubEnv('VITE_MICROSOFT_CLIENT_ID', 'test-cid');
    msalMocks.loginPopup.mockResolvedValue({ idToken: 'fake.jwt' });
    server.use(
      http.post(`${API_BASE}/auth/microsoft`, () =>
        HttpResponse.json(
          { error: { code: 'unauthorized', message: 'Microsoft token rejected.' } },
          { status: 401 },
        ),
      ),
    );

    renderLoginAtRoute();

    const button = await screen.findByRole('button', { name: /continue with microsoft/i });
    await userEvent.click(button);

    await waitFor(() => {
      expect(screen.getByText('Microsoft token rejected.')).toBeInTheDocument();
    });
    // The backend rejected → no navigation.
    expect(screen.queryByText('Inbox landing')).not.toBeInTheDocument();
  });

  it('treats a cancelled / timed-out popup as a soft cancellation', async () => {
    vi.stubEnv('VITE_MICROSOFT_CLIENT_ID', 'test-cid');
    msalMocks.loginPopup.mockRejectedValue({ errorCode: 'timed_out' });

    renderLoginAtRoute();

    const button = await screen.findByRole('button', { name: /continue with microsoft/i });
    await userEvent.click(button);

    await waitFor(() => {
      expect(screen.getByText('Inicio de sesión cancelado.')).toBeInTheDocument();
    });
    expect(screen.queryByText('Inbox landing')).not.toBeInTheDocument();
  });
});
