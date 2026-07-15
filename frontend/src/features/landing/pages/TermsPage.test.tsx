import { QueryClientProvider } from '@tanstack/react-query';
import { render, screen, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it } from 'vitest';

import AuthProvider from '../../../app/providers/AuthProvider';
import { I18nProvider, LANG_STORAGE_KEY } from '../../../lib/i18n';
import { server } from '../../../test/msw/server';
import { createTestQueryClient } from '../../../test/renderWithProviders';
import TermsPage from './TermsPage';

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

// /terms is a public sibling of /login — it never passes through RequireAuth,
// so an anonymous visitor must see the document, not the login screen.
function renderTerms() {
  return render(
    <QueryClientProvider client={createTestQueryClient()}>
      <AuthProvider>
        <I18nProvider>
          <MemoryRouter initialEntries={['/terms']}>
            <Routes>
              <Route path="/login" element={<div>Login screen</div>} />
              <Route path="/terms" element={<TermsPage />} />
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

describe('TermsPage', () => {
  it('renders the terms of service without a session', async () => {
    forceAnonymous();
    renderTerms();

    expect(
      await screen.findByRole('heading', { level: 1, name: 'Terms of Service' }),
    ).toBeInTheDocument();
    // The terms link into the privacy policy (section 4). Scoped to the
    // document body because the footer carries a second "Privacy Policy" link.
    const article = screen.getByRole('article');
    expect(within(article).getByRole('link', { name: 'Privacy Policy' })).toHaveAttribute(
      'href',
      '/privacy',
    );
    expect(screen.queryByText('Login screen')).not.toBeInTheDocument();
  });

  it('renders the Spanish version when the stored language is es', async () => {
    forceAnonymous();
    window.localStorage.setItem(LANG_STORAGE_KEY, 'es');
    renderTerms();

    expect(
      await screen.findByRole('heading', { level: 1, name: 'Términos de servicio' }),
    ).toBeInTheDocument();
  });
});
