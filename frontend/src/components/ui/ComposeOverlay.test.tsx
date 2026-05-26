/**
 * Component-level tests for ``ComposeOverlay`` covering the two
 * behavioural changes wired in for silent-bootstrap drag-and-drop:
 *
 *  - ``handleDragOver`` and ``handleDrop`` always call ``preventDefault``
 *    on file drags, even when ``attachmentsEnabled`` is false. Without
 *    that guard the browser would navigate to the dropped file and lose
 *    the entire composition.
 *  - The new ``accountSelectorLocked`` prop disables the account
 *    dropdown trigger and hides both the chevron and the menu, so the
 *    user cannot reroute the silent draft to a different account
 *    halfway through the composition.
 */

import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import ComposeOverlay from './ComposeOverlay';
import type { AccountOut } from '../../api/types/dto';

type ComposeAccount = Pick<
  AccountOut,
  'account_id' | 'provider' | 'email_address' | 'display_label'
>;

const ACCOUNTS: ComposeAccount[] = [
  {
    account_id: 'acc_1',
    provider: 'gmail',
    email_address: 'one@example.com',
    display_label: 'One',
  },
  {
    account_id: 'acc_2',
    provider: 'outlook',
    email_address: 'two@example.com',
    display_label: 'Two',
  },
];

type RenderOpts = Partial<React.ComponentProps<typeof ComposeOverlay>>;

function renderOverlay(overrides: RenderOpts = {}) {
  const onAddFiles = overrides.onAddFiles ?? vi.fn();
  const onRemoveAttachment = overrides.onRemoveAttachment ?? vi.fn();
  const onSelectedAccountChange = overrides.onSelectedAccountChange ?? vi.fn();
  const onClose = overrides.onClose ?? vi.fn();
  const noop = () => {};

  const defaults = {
    mode: 'new_email' as const,
    accounts: ACCOUNTS,
    selectedAccountId: 'acc_1',
    onSelectedAccountChange,
    to: '',
    onToChange: noop,
    cc: '',
    onCcChange: noop,
    bcc: '',
    onBccChange: noop,
    subject: '',
    onSubjectChange: noop,
    body: '',
    onBodyChange: noop,
    sending: false,
    saving: false,
    error: null,
    canSendEmail: true,
    canSaveDraft: true,
    canSendDraft: true,
    onSendEmail: noop,
    onSaveDraft: noop,
    onSendDraft: noop,
    onClose,
    attachmentsEnabled: true,
    accountSelectorLocked: false,
    attachmentChips: [],
    attachmentTotalSize: 0,
    onAddFiles,
    onRemoveAttachment,
  };

  const utils = render(<ComposeOverlay {...defaults} {...overrides} />);
  return { ...utils, onAddFiles, onSelectedAccountChange, onClose };
}

/**
 * Build a minimal ``DataTransfer``-shaped object good enough for the
 * three things ``ComposeOverlay`` actually reads: ``types`` (to detect
 * a file drag) and ``files`` (to forward to ``onAddFiles``). jsdom's
 * synthetic events do not carry one by default, so we hand one in
 * via ``fireEvent.dragOver/drop``'s second argument.
 */
function makeDataTransfer(files: File[] = []): DataTransfer {
  return {
    types: ['Files'],
    files: files as unknown as FileList,
  } as unknown as DataTransfer;
}

describe('ComposeOverlay — drag-and-drop preventDefault', () => {
  it('calls preventDefault on dragover/drop with attachmentsEnabled=true and forwards the files', () => {
    const onAddFiles = vi.fn();
    const { container } = renderOverlay({ onAddFiles, attachmentsEnabled: true });

    // The overlay's outer container is the first child of the testing root.
    const overlay = container.firstChild as HTMLElement;
    expect(overlay).toBeTruthy();

    const file = new File([new Uint8Array(100)], 'a.pdf', { type: 'application/pdf' });

    const dragOverDefaultPrevented = !fireEvent.dragOver(overlay, {
      dataTransfer: makeDataTransfer([file]),
    });
    expect(dragOverDefaultPrevented).toBe(true);

    const dropDefaultPrevented = !fireEvent.drop(overlay, {
      dataTransfer: makeDataTransfer([file]),
    });
    expect(dropDefaultPrevented).toBe(true);

    expect(onAddFiles).toHaveBeenCalledTimes(1);
    expect(onAddFiles).toHaveBeenCalledWith([file]);
  });

  it('still calls preventDefault when attachmentsEnabled=false but never invokes onAddFiles', () => {
    // The defensive guard in handleDragOver / handleDrop must short-circuit
    // the browser's default "open the dropped file" behaviour even when
    // the composer is momentarily not ready to accept attachments. Losing
    // an entire composition because a stray drop hit the window before the
    // account loaded would be data loss.
    const onAddFiles = vi.fn();
    const { container } = renderOverlay({ onAddFiles, attachmentsEnabled: false });

    const overlay = container.firstChild as HTMLElement;
    const file = new File([new Uint8Array(100)], 'a.pdf', { type: 'application/pdf' });

    const dragOverDefaultPrevented = !fireEvent.dragOver(overlay, {
      dataTransfer: makeDataTransfer([file]),
    });
    expect(dragOverDefaultPrevented).toBe(true);

    const dropDefaultPrevented = !fireEvent.drop(overlay, {
      dataTransfer: makeDataTransfer([file]),
    });
    expect(dropDefaultPrevented).toBe(true);

    // Drop is intentionally swallowed: the overlay is in a transient
    // "no account loaded" state where the upload would have nowhere to land.
    expect(onAddFiles).not.toHaveBeenCalled();
  });

  it('does not preventDefault when the drag does not carry files (e.g. text dragged from the page)', () => {
    const { container } = renderOverlay({ attachmentsEnabled: true });
    const overlay = container.firstChild as HTMLElement;

    const dt = {
      types: ['text/plain'],
      files: [] as unknown as FileList,
    } as unknown as DataTransfer;

    const defaultPrevented = !fireEvent.dragOver(overlay, { dataTransfer: dt });
    // The handler returns before preventDefault — the browser owns this
    // gesture (text selection / link drag), not our overlay.
    expect(defaultPrevented).toBe(false);
  });
});

describe('ComposeOverlay — account selector locked', () => {
  it('renders the chevron and opens the menu on click when not locked', async () => {
    const onSelectedAccountChange = vi.fn();
    renderOverlay({ accountSelectorLocked: false, onSelectedAccountChange });

    // Chevron icon is purely decorative; reach the trigger button via
    // the displayed account email (the trigger label).
    const trigger = screen.getByRole('button', { name: /one@example.com/i });
    expect(trigger).not.toBeDisabled();

    const user = userEvent.setup();
    await user.click(trigger);

    // The menu lists every account with its label as the button text.
    expect(screen.getByRole('button', { name: /two@example.com/i })).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /two@example.com/i }));
    expect(onSelectedAccountChange).toHaveBeenCalledWith('acc_2');
  });

  it('disables the trigger and never opens the menu when locked', async () => {
    const onSelectedAccountChange = vi.fn();
    renderOverlay({ accountSelectorLocked: true, onSelectedAccountChange });

    const trigger = screen.getByRole('button', { name: /one@example.com/i });
    expect(trigger).toBeDisabled();

    // Even if the user manages to click it (e.g. focus + Enter), the menu
    // does not render because both conditions ``selectorOpen`` AND
    // ``!accountSelectorLocked`` must hold.
    const user = userEvent.setup();
    await user.click(trigger);
    expect(screen.queryByRole('button', { name: /two@example.com/i })).not.toBeInTheDocument();
    expect(onSelectedAccountChange).not.toHaveBeenCalled();
  });
});

describe('ComposeOverlay — title by mode', () => {
  // The reply / reply_all / forward modes were added in the Reply
  // feature. Each must render its own header label so the user always
  // sees which action they are about to perform — the only visual
  // distinction between the four composer modes ``edit_draft`` and
  // ``reply``/``reply_all``/``forward`` (the button set is identical).
  it.each([
    ['reply', 'Responder'],
    ['reply_all', 'Responder a todos'],
    ['forward', 'Reenviar'],
  ] as const)('mode=%s renders the %s title', (mode, title) => {
    renderOverlay({ mode });
    expect(screen.getByText(title)).toBeInTheDocument();
  });

  it('mode=reply shows the same button set as edit_draft (Send draft + Save)', () => {
    // Reply / Reply All / Forward share their button set with
    // edit_draft: both can be saved AND sent (no "Send email" path,
    // because the draft already exists on the provider).
    renderOverlay({ mode: 'reply' });
    // Save-draft button present.
    expect(screen.getByRole('button', { name: /guardar/i })).toBeInTheDocument();
    // Send-draft button present.
    expect(screen.getByRole('button', { name: /enviar/i })).toBeInTheDocument();
  });

  it('mode=forward keeps the same buttons as reply', () => {
    renderOverlay({ mode: 'forward' });
    expect(screen.getByRole('button', { name: /guardar/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /enviar/i })).toBeInTheDocument();
  });
});
