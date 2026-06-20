/**
 * Integration test for the language preference page. Renders inside the real
 * I18nProvider (via renderWithProviders) so switching the language exercises
 * the actual context + persistence, not a stub.
 */

import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { renderWithProviders } from '../../../test/renderWithProviders';
import { LANG_STORAGE_KEY } from '../../../lib/i18n';
import PreferencesPage from './PreferencesPage';

beforeEach(() => {
  window.localStorage.setItem(LANG_STORAGE_KEY, 'es');
});

afterEach(() => {
  window.localStorage.clear();
});

describe('PreferencesPage', () => {
  it('renders the page heading in the persisted language (Spanish)', () => {
    renderWithProviders(<PreferencesPage />);
    expect(screen.getByRole('heading', { name: 'Idioma' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Español' })).toHaveAttribute('aria-pressed', 'true');
  });

  it('switches a representative text from Spanish to English and persists the choice', async () => {
    renderWithProviders(<PreferencesPage />);
    expect(
      screen.getByText('Elige el idioma de la interfaz. No afecta al contenido de los correos.'),
    ).toBeInTheDocument();

    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: 'English' }));

    await waitFor(() =>
      expect(
        screen.getByText('Choose the interface language. It does not affect email content.'),
      ).toBeInTheDocument(),
    );
    // The English option is now the active one and the choice is persisted.
    expect(screen.getByRole('button', { name: 'English' })).toHaveAttribute('aria-pressed', 'true');
    expect(window.localStorage.getItem(LANG_STORAGE_KEY)).toBe('en');
  });
});
