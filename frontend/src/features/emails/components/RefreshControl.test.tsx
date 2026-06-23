/**
 * Component tests for RefreshControl.
 *
 * Presentational: it receives ``onRefresh`` / ``syncing`` / ``lastSyncedAt`` /
 * ``hasError`` as props and consumes the i18n context for its copy. We render
 * it inside the production provider stack (so ``useTranslation`` resolves) and
 * assert on what the user sees + the click contract.
 *
 * ⚠️ The button carries ``aria-label={t('common.refreshAria')}``, so its
 * ACCESSIBLE NAME is "Buscar correo nuevo" (es), NOT the visible "Refrescar"
 * text — an aria-label wins over text content in the accessible-name
 * computation. Hence: locate the button by role with name 'Buscar correo
 * nuevo'; assert the visible label via getByText('Refrescar').
 */

import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { renderWithProviders } from '../../../test/renderWithProviders';
import { pinTestLang } from '../../../test/i18nTestLang';
import RefreshControl from './RefreshControl';

// Pin Spanish so the fixed Spanish copy assertions hold (jsdom defaults to en).
pinTestLang('es');

describe('RefreshControl', () => {
  it('renders the refresh button with its visible label and accessible name', () => {
    renderWithProviders(
      <RefreshControl onRefresh={() => {}} syncing={false} lastSyncedAt={null} />,
    );

    // Accessible name comes from the aria-label, not the visible text.
    expect(screen.getByRole('button', { name: 'Buscar correo nuevo' })).toBeInTheDocument();
    // The visible label is the localized "Refrescar".
    expect(screen.getByText('Refrescar')).toBeInTheDocument();
  });

  it('calls onRefresh when the button is clicked', async () => {
    const onRefresh = vi.fn();
    renderWithProviders(
      <RefreshControl onRefresh={onRefresh} syncing={false} lastSyncedAt={null} />,
    );

    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: 'Buscar correo nuevo' }));

    expect(onRefresh).toHaveBeenCalledTimes(1);
  });

  it('disables the button and shows the syncing label while syncing', () => {
    renderWithProviders(<RefreshControl onRefresh={() => {}} syncing lastSyncedAt={null} />);

    const button = screen.getByRole('button', { name: 'Buscar correo nuevo' });
    expect(button).toBeDisabled();
    // Active state swaps the visible label to the reused "Sincronizando…" key
    // and spins the icon.
    expect(screen.getByText('Sincronizando…')).toBeInTheDocument();
    expect(button.querySelector('.animate-spin')).not.toBeNull();
  });

  it('shows the never-synced status when lastSyncedAt is null', () => {
    renderWithProviders(
      <RefreshControl onRefresh={() => {}} syncing={false} lastSyncedAt={null} />,
    );

    expect(screen.getByText('Sin sincronizar todavía')).toBeInTheDocument();
  });

  it('shows the relative "last updated" status when a timestamp is present', () => {
    // A timestamp at (effectively) now renders the relative "ahora" via
    // formatRelativeTime; the surrounding copy is the lastSync template.
    renderWithProviders(
      <RefreshControl onRefresh={() => {}} syncing={false} lastSyncedAt={Date.now()} />,
    );

    expect(screen.getByText('Última actualización: ahora')).toBeInTheDocument();
  });

  it('surfaces the generic failure notice (not a raw backend message) when hasError is true', () => {
    renderWithProviders(
      <RefreshControl onRefresh={() => {}} syncing={false} lastSyncedAt={Date.now()} hasError />,
    );

    // The error notice wins over the last-sync status and is the localized
    // generic string, never the backend's raw error text.
    const notice = screen.getByText('No se pudo actualizar');
    expect(notice).toBeInTheDocument();
    expect(notice).toHaveClass('text-red-600');
    // The last-sync status is replaced, not stacked.
    expect(screen.queryByText(/Última actualización:/)).not.toBeInTheDocument();
  });
});
