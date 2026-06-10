import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

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

  it('shows only the "Para" column with the recipient in an individual SENT view', () => {
    // This is the property the in: column-override solution relies on: the
    // single individual column flips to "Para" when isSent is true.
    render(
      <EmailTable
        emails={[makeEmail({ to_email: 'recipient@example.com', box: 'SENT' })]}
        accounts={[accountFixture]}
        loading={false}
        view="individual"
        isSent={true}
      />,
    );
    expect(screen.getByText('Para')).toBeInTheDocument();
    expect(screen.queryByText('De')).not.toBeInTheDocument();
    expect(screen.getByText('recipient@example.com')).toBeInTheDocument();
  });

  it('shows only the "De" column with the sender in an individual received view', () => {
    render(
      <EmailTable
        emails={[makeEmail({ from_email: 'sender@example.com', box: 'ALL_MAIL' })]}
        accounts={[accountFixture]}
        loading={false}
        view="individual"
        isSent={false}
      />,
    );
    expect(screen.getByText('De')).toBeInTheDocument();
    expect(screen.queryByText('Para')).not.toBeInTheDocument();
    expect(screen.getByText('sender@example.com')).toBeInTheDocument();
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

  describe('conversation mode', () => {
    it('shows the thread message-count chip only when the count is > 1', () => {
      const { rerender } = render(
        <EmailTable
          emails={[makeEmail({ subject: 'Threaded subject', thread_message_count: 3 })]}
          accounts={[accountFixture]}
          loading={false}
          view="individual"
          isSent={false}
          conversationMode
          onOpen={() => {}}
        />,
      );
      // The count renders as a discrete chip next to the subject.
      expect(screen.getByText('3')).toBeInTheDocument();

      // A single-message thread shows no chip.
      rerender(
        <EmailTable
          emails={[makeEmail({ subject: 'Single subject', thread_message_count: 1 })]}
          accounts={[accountFixture]}
          loading={false}
          view="individual"
          isSent={false}
          conversationMode
          onOpen={() => {}}
        />,
      );
      expect(screen.queryByText('1', { selector: 'span' })).not.toBeInTheDocument();
    });

    it('shows the base subject without the Re:/Fwd: prefix stack on thread rows', () => {
      const { rerender } = render(
        <EmailTable
          emails={[makeEmail({ subject: 'Re: Fwd: Presupuesto', thread_message_count: 2 })]}
          accounts={[accountFixture]}
          loading={false}
          view="individual"
          isSent={false}
          conversationMode
          onOpen={() => {}}
        />,
      );
      expect(screen.getByText('Presupuesto')).toBeInTheDocument();
      expect(screen.queryByText('Re: Fwd: Presupuesto')).not.toBeInTheDocument();

      // Outside conversation mode the subject is rendered verbatim.
      rerender(
        <EmailTable
          emails={[makeEmail({ subject: 'Re: Fwd: Presupuesto' })]}
          accounts={[accountFixture]}
          loading={false}
          view="individual"
          isSent={false}
        />,
      );
      expect(screen.getByText('Re: Fwd: Presupuesto')).toBeInTheDocument();
    });

    it('renders no selection checkbox and no interactive favourite button (read-only row)', () => {
      render(
        <EmailTable
          emails={[makeEmail({ is_favorite: true })]}
          accounts={[accountFixture]}
          loading={false}
          view="individual"
          isSent={false}
          conversationMode
          onOpen={() => {}}
        />,
      );
      // No selection control (the page does not pass isSelected/onToggle).
      expect(
        screen.queryByRole('checkbox', { name: 'Seleccionar correo' }),
      ).not.toBeInTheDocument();
      // The favourite is an indicator, not a button.
      expect(screen.queryByRole('button', { name: 'Quitar de favoritos' })).not.toBeInTheDocument();
      expect(
        screen.queryByRole('button', { name: 'Marcar como favorito' }),
      ).not.toBeInTheDocument();
    });

    it('opens the conversation with the representative row via onOpen', async () => {
      const onOpen = vi.fn();
      const rep = makeEmail({ provider_message_id: 'rep_1', subject: 'Open me' });
      render(
        <EmailTable
          emails={[rep]}
          accounts={[accountFixture]}
          loading={false}
          view="individual"
          isSent={false}
          conversationMode
          onOpen={onOpen}
        />,
      );
      const user = userEvent.setup();
      await user.click(screen.getByText('Open me'));
      expect(onOpen).toHaveBeenCalledTimes(1);
      expect(onOpen.mock.calls[0][0]).toMatchObject({ provider_message_id: 'rep_1' });
    });

    it('renders the clip from the aggregated has_attachments of the thread row', () => {
      const { rerender } = render(
        <EmailTable
          emails={[makeEmail({ has_attachments: true, subject: 'With clip' })]}
          accounts={[accountFixture]}
          loading={false}
          view="individual"
          isSent={false}
          conversationMode
          onOpen={() => {}}
        />,
      );
      // The aggregated clip is shown for the row (bool_or(has_attachments)).
      expect(screen.getByLabelText('Tiene adjuntos')).toBeInTheDocument();

      rerender(
        <EmailTable
          emails={[makeEmail({ has_attachments: false, subject: 'No clip' })]}
          accounts={[accountFixture]}
          loading={false}
          view="individual"
          isSent={false}
          conversationMode
          onOpen={() => {}}
        />,
      );
      expect(screen.queryByLabelText('Tiene adjuntos')).not.toBeInTheDocument();
    });
  });
});
