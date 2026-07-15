/**
 * Component tests for AccountCard's live backfill counter.
 *
 * Presentational: the card receives the backfill status + count as props
 * (resolved by the page from the useBackfillStatus Map). The counter is shown
 * ONLY while the backfill is in flight (pending/running); completed, failed and
 * absent all collapse to the normal render — a failed backfill shows no error
 * notice by design (MVP, backend §4.4).
 */

import { screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { renderWithProviders } from '../../../test/renderWithProviders';
import { pinTestLang } from '../../../test/i18nTestLang';
import AccountCard from './AccountCard';

pinTestLang('es');

const account = {
  account_id: 'a_1',
  mailbox_id: 'mb_1',
  provider: 'gmail',
  display_label: 'Gmail',
  config: {},
  email_address: 'one@example.com',
  signature_html: null,
};

describe('AccountCard — backfill counter', () => {
  it('shows the live "Cargando… N correos" counter while running', () => {
    // A small count keeps toLocaleString locale-stable (no grouping separator).
    renderWithProviders(
      <AccountCard
        account={account}
        status="ready"
        backfillStatus="running"
        backfillFetchedCount={12}
      />,
    );

    const notice = screen.getByText(/Cargando/);
    expect(notice.textContent).toContain('12');
  });

  it('shows the counter while pending too', () => {
    renderWithProviders(
      <AccountCard
        account={account}
        status="ready"
        backfillStatus="pending"
        backfillFetchedCount={0}
      />,
    );
    expect(screen.getByText(/Cargando/)).toBeInTheDocument();
  });

  it('does not show the counter when the backfill completed', () => {
    renderWithProviders(
      <AccountCard
        account={account}
        status="ready"
        backfillStatus="completed"
        backfillFetchedCount={100000}
      />,
    );
    expect(screen.queryByText(/Cargando/)).not.toBeInTheDocument();
  });

  it('does not show the counter — nor any error notice — when the backfill failed', () => {
    renderWithProviders(
      <AccountCard
        account={account}
        status="ready"
        backfillStatus="failed"
        backfillFetchedCount={42}
      />,
    );
    // Failed backfill retires the label with no error surfaced (MVP).
    expect(screen.queryByText(/Cargando/)).not.toBeInTheDocument();
    expect(screen.queryByText('Error al sincronizar')).not.toBeInTheDocument();
  });

  it('renders normally when no backfill props are supplied', () => {
    renderWithProviders(<AccountCard account={account} status="ready" />);
    expect(screen.getByText('one@example.com')).toBeInTheDocument();
    expect(screen.queryByText(/Cargando/)).not.toBeInTheDocument();
  });
});
