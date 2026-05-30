import { QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { afterEach, describe, expect, it, vi } from 'vitest';

import AuthProvider from './AuthProvider';
import { useAuth } from './AuthContext';
import { server } from '../../test/msw/server';
import { createTestQueryClient } from '../../test/renderWithProviders';

const API_BASE = 'http://localhost:8000';

function Probe() {
  const { user, loading } = useAuth();
  if (loading) return <div>loading</div>;
  return <div>{user ? `user: ${user.email}` : 'anon'}</div>;
}

function renderWithRealAuthProvider() {
  return render(
    <QueryClientProvider client={createTestQueryClient()}>
      <AuthProvider>
        <Probe />
      </AuthProvider>
    </QueryClientProvider>,
  );
}

function forceUnauthenticatedSession() {
  server.use(
    http.get(`${API_BASE}/auth/me`, () =>
      HttpResponse.json(
        { error: { code: 'unauthorized', message: 'Not authenticated' } },
        { status: 401 },
      ),
    ),
  );
}

describe('AuthProvider — DEV auto-login', () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it('auto-authenticates via dev-login when VITE_DEV_AUTO_LOGIN is enabled and no session exists', async () => {
    vi.stubEnv('VITE_DEV_AUTO_LOGIN', 'true');
    forceUnauthenticatedSession();

    renderWithRealAuthProvider();

    // The default /auth/dev-login MSW handler resolves with the dev user.
    await waitFor(() => {
      expect(screen.getByText('user: dev@example.com')).toBeInTheDocument();
    });
  });

  it('stays unauthenticated when VITE_DEV_AUTO_LOGIN is not set', async () => {
    forceUnauthenticatedSession();

    renderWithRealAuthProvider();

    await waitFor(() => {
      expect(screen.getByText('anon')).toBeInTheDocument();
    });
  });

  it('does not call dev-login when VITE_DEV_AUTO_LOGIN is enabled but a session already exists', async () => {
    vi.stubEnv('VITE_DEV_AUTO_LOGIN', 'true');
    let devLoginCalls = 0;
    server.use(
      http.post(`${API_BASE}/auth/dev-login`, () => {
        devLoginCalls += 1;
        return HttpResponse.json({
          user: { user_id: 'u_dev', email: 'dev@example.com', name: 'Dev User', avatar_url: null },
          message: 'Dev login successful.',
        });
      }),
    );

    renderWithRealAuthProvider();

    // Default /auth/me handler returns the authenticated tester user.
    await waitFor(() => {
      expect(screen.getByText('user: tester@example.com')).toBeInTheDocument();
    });
    expect(devLoginCalls).toBe(0);
  });
});
