import { QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it } from 'vitest';

import AuthProvider from '../../../app/providers/AuthProvider';
import { I18nProvider, LANG_STORAGE_KEY } from '../../../lib/i18n';
import { server } from '../../../test/msw/server';
import { createTestQueryClient } from '../../../test/renderWithProviders';
import PrivacyPage from './PrivacyPage';

const API_BASE = 'http://localhost:8000';

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

// /privacy is a public sibling of /login — it never passes through RequireAuth,
// so an anonymous visitor must see the document, not the login screen.
function renderPrivacy() {
  return render(
    <QueryClientProvider client={createTestQueryClient()}>
      <AuthProvider>
        <I18nProvider>
          <MemoryRouter initialEntries={['/privacy']}>
            <Routes>
              <Route path="/login" element={<div>Login screen</div>} />
              <Route path="/privacy" element={<PrivacyPage />} />
            </Routes>
          </MemoryRouter>
        </I18nProvider>
      </AuthProvider>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  window.localStorage.removeItem(LANG_STORAGE_KEY);
});

describe('PrivacyPage', () => {
  it('renders the privacy policy without a session', async () => {
    forceAnonymous();
    renderPrivacy();

    expect(
      await screen.findByRole('heading', { level: 1, name: 'Privacy Policy' }),
    ).toBeInTheDocument();
    expect(screen.getByText('Last updated: July 15, 2026')).toBeInTheDocument();
    expect(screen.queryByText('Login screen')).not.toBeInTheDocument();
  });

  it('renders the Spanish version when the stored language is es', async () => {
    forceAnonymous();
    window.localStorage.setItem(LANG_STORAGE_KEY, 'es');
    renderPrivacy();

    expect(
      await screen.findByRole('heading', { level: 1, name: 'Política de privacidad' }),
    ).toBeInTheDocument();
  });
});
