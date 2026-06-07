import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import EmailTable from './EmailTable';
import type { AccountOut, EmailMetadataOut } from '../../../api/types/dto';

// EmailTable is presentational: it renders the header bar (range +
// pagination controls), the column headers and the rows entirely from its
// props. No MSW, no router, no query client — a plain render is enough.
// EmailPagination's own behaviour is covered in EmailPagination.test.tsx;
// here we only assert what EmailTable adds: the "from–to de total" range
// that replaced the bare "{n} correos" counter, and the conditions under
// which the controls appear.

const accountFixture: AccountOut = {
  account_id: 'a_1',
  mailbox_id: 'mb_1',
  provider: 'gmail',
  display_label: 'Gmail',
  config: {},
  email_address: 'alice@example.com',
};

function makeEmail(overrides: Partial<EmailMetadataOut> = {}): EmailMetadataOut {
  return {
    provider_message_id: 'm_1',
    account_id: 'a_1',
    mailbox_id: 'mb_1',
    thread_id: null,
    from_email: 'bob@example.com',
    from_name: 'Bob',
    to_email: 'alice@example.com',
    to_name: null,
    subject: 'Hello world',
    received_at: new Date('2024-01-10T09:00:00Z').toISOString(),
    is_read: true,
    box: 'ALL_MAIL',
    has_attachments: false,
    is_favorite: false,
    ...overrides,
  };
}

describe('EmailTable', () => {
  it('shows the pagination range on the left instead of the bare count', () => {
    render(
      <EmailTable
        emails={[makeEmail()]}
        accounts={[accountFixture]}
        loading={false}
        view="unified"
        isSent={false}
        page={1}
        pageSize={50}
        total={130}
        onPageChange={() => {}}
      />,
    );
    expect(screen.getByText('1–50 de 130')).toBeInTheDocument();
    expect(screen.queryByText('1 correos')).not.toBeInTheDocument();
  });

  it('caps the range upper bound at total and groups thousands on the last page', () => {
    // 1234 total / 50 → 25 pages, the last one holding 1201–1234.
    render(
      <EmailTable
        emails={[makeEmail()]}
        accounts={[accountFixture]}
        loading={false}
        view="unified"
        isSent={false}
        page={25}
        pageSize={50}
        total={1234}
        onPageChange={() => {}}
      />,
    );
    expect(screen.getByText('1.201–1.234 de 1.234')).toBeInTheDocument();
  });

  it('renders the navigation controls when pagination is wired and total > 0', () => {
    render(
      <EmailTable
        emails={[makeEmail()]}
        accounts={[accountFixture]}
        loading={false}
        view="unified"
        isSent={false}
        page={1}
        pageSize={50}
        total={130}
        onPageChange={() => {}}
      />,
    );
    expect(screen.getByRole('button', { name: 'Página siguiente' })).toBeInTheDocument();
  });

  it('renders no controls and a "0 correos" label for an empty result', () => {
    render(
      <EmailTable
        emails={[]}
        accounts={[accountFixture]}
        loading={false}
        view="unified"
        isSent={false}
        page={1}
        pageSize={50}
        total={0}
        onPageChange={() => {}}
      />,
    );
    expect(screen.getByText('0 correos')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Página siguiente' })).not.toBeInTheDocument();
  });

  it('falls back to the legacy "{n} correos" counter when pagination is not wired', () => {
    render(
      <EmailTable
        emails={[
          makeEmail({ provider_message_id: 'm_1' }),
          makeEmail({ provider_message_id: 'm_2' }),
        ]}
        accounts={[accountFixture]}
        loading={false}
        view="unified"
        isSent={false}
      />,
    );
    expect(screen.getByText('2 correos')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Página siguiente' })).not.toBeInTheDocument();
  });

  it('keeps the pager visible but hides the range while a bulk selection is active', () => {
    // The bulk bar takes over the left side (so the range is hidden), but the
    // pager stays on the right so a selection can be carried across pages.
    render(
      <EmailTable
        emails={[makeEmail()]}
        accounts={[accountFixture]}
        loading={false}
        view="unified"
        isSent={false}
        hasSelection
        bulkBar={<span>Acciones en lote</span>}
        page={1}
        pageSize={50}
        total={130}
        onPageChange={() => {}}
      />,
    );
    expect(screen.getByText('Acciones en lote')).toBeInTheDocument();
    expect(screen.queryByText('1–50 de 130')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Página siguiente' })).toBeInTheDocument();
  });
});
