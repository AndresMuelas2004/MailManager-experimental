import { render as rtlRender, screen, type RenderOptions } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactElement } from 'react';
import { describe, expect, it, vi } from 'vitest';

import ConversationMessageCard from './ConversationMessageCard';
import { I18nProvider } from '../../../lib/i18n';
import { pinTestLang } from '../../../test/i18nTestLang';
import type { EmailMetadataOut } from '../../../api/types/dto';

// ConversationMessageCard reads its folder labels through ``t()``, so it must
// render inside the real I18nProvider. A local ``render`` injects it for every
// call site; Spanish is pinned so the existing Spanish label assertions hold.
pinTestLang('es');

function render(ui: ReactElement, options?: Omit<RenderOptions, 'wrapper'>) {
  return rtlRender(ui, { wrapper: I18nProvider, ...options });
}

// ConversationMessageCard is presentational: its collapsed header renders
// entirely from props and mounts no body (so no fetch, no MSW). These tests
// cover what the header shows — the folder label for messages living outside
// ALL_MAIL, the unread/favourite indicators — and that the collapsed header
// never shows an attachment clip (inside a ConversationOut every message's
// has_attachments is always false, so reading it would be a dead indicator).

function makeMessage(overrides: Partial<EmailMetadataOut> = {}): EmailMetadataOut {
  return {
    provider_message_id: 'm_1',
    account_id: 'a_1',
    mailbox_id: 'mb_1',
    thread_id: 't_1',
    from_email: 'bob@example.com',
    from_name: 'Bob',
    to_email: 'me@example.com',
    to_name: null,
    subject: 'Subject',
    received_at: new Date('2024-01-10T09:00:00Z').toISOString(),
    is_read: true,
    box: 'ALL_MAIL',
    has_attachments: false,
    is_favorite: false,
    thread_message_count: 1,
    folders: [],
    ...overrides,
  };
}

describe('ConversationMessageCard (collapsed header)', () => {
  it('shows a folder label for a message that lives outside ALL_MAIL', () => {
    render(
      <ConversationMessageCard
        message={makeMessage({ box: 'SENT' })}
        expanded={false}
        onToggle={() => {}}
      />,
    );
    expect(screen.getByText('Enviado')).toBeInTheDocument();
  });

  it.each([
    ['SPAM', 'Spam'],
    ['TRASH', 'Papelera'],
    ['ARCHIVE', 'Archivado'],
  ])('labels a %s message as %s', (box, label) => {
    render(
      <ConversationMessageCard
        message={makeMessage({ box })}
        expanded={false}
        onToggle={() => {}}
      />,
    );
    expect(screen.getByText(label)).toBeInTheDocument();
  });

  it('shows no folder label for an ALL_MAIL message (the normal inbox location)', () => {
    render(
      <ConversationMessageCard
        message={makeMessage({ box: 'ALL_MAIL' })}
        expanded={false}
        onToggle={() => {}}
      />,
    );
    expect(screen.queryByText('Enviado')).not.toBeInTheDocument();
    expect(screen.queryByText('Spam')).not.toBeInTheDocument();
    expect(screen.queryByText('Papelera')).not.toBeInTheDocument();
  });

  it('marks an unread message and a favourite message in the header', () => {
    render(
      <ConversationMessageCard
        message={makeMessage({ is_read: false, is_favorite: true })}
        expanded={false}
        onToggle={() => {}}
      />,
    );
    expect(screen.getByLabelText('No leído')).toBeInTheDocument();
    expect(screen.getByLabelText('Favorito')).toBeInTheDocument();
  });

  it('does not render an attachment clip even when has_attachments is true (B.lazy dead field)', () => {
    // Inside a ConversationOut the backend forces has_attachments=false, but
    // the card must not read it regardless — the real clip surfaces only on
    // expand via the body's EmailContentOut.attachments.
    render(
      <ConversationMessageCard
        message={makeMessage({ has_attachments: true })}
        expanded={false}
        onToggle={() => {}}
      />,
    );
    expect(screen.queryByLabelText('Tiene adjuntos')).not.toBeInTheDocument();
  });

  it('toggles on header click', async () => {
    const onToggle = vi.fn();
    render(
      <ConversationMessageCard
        message={makeMessage({ from_name: 'Bob' })}
        expanded={false}
        onToggle={onToggle}
      />,
    );
    const user = userEvent.setup();
    await user.click(screen.getByText(/Bob/));
    expect(onToggle).toHaveBeenCalledTimes(1);
  });
});
