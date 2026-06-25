/**
 * Component tests for ``BulkActionsBar`` — which actions it offers per box.
 *
 * The bar is presentational: it renders entirely from props plus the
 * ``EMAIL_BOX_CONFIG[box].allowedBulkActions`` whitelist. These tests pin the
 * Archive feature's contract — "Archivar" appears in ALL_MAIL (and Favoritos,
 * which mounts the bar with box=ALL_MAIL), "Desarchivar" appears in ARCHIVE,
 * and neither leaks into SENT / SPAM / TRASH — and that clicking each fires the
 * matching ``onAction`` payload. Labels are read through ``t()`` so the bar
 * renders inside the real I18nProvider; Spanish is pinned for fixed assertions.
 */

import { render as rtlRender, screen, type RenderOptions } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactElement } from 'react';
import { describe, expect, it, vi } from 'vitest';

import BulkActionsBar from './BulkActionsBar';
import { I18nProvider } from '../../../lib/i18n';
import { pinTestLang } from '../../../test/i18nTestLang';
import type { EmailBox } from '../../../lib/types';
import type { BulkAction } from '../types';

pinTestLang('es');

function render(ui: ReactElement, options?: Omit<RenderOptions, 'wrapper'>) {
  return rtlRender(ui, { wrapper: I18nProvider, ...options });
}

function renderBar(box: EmailBox, onAction = vi.fn()) {
  render(
    <BulkActionsBar
      selectedCount={2}
      box={box}
      readToggleTarget="mark_read"
      disabled={false}
      onClear={() => {}}
      onAction={onAction}
    />,
  );
  return onAction;
}

const ARCHIVE_LABEL = 'Archivar'; // bulk.archive
const UNARCHIVE_LABEL = 'Desarchivar'; // bulk.unarchive

describe('BulkActionsBar — archive / unarchive per box', () => {
  it('offers "Archivar" but not "Desarchivar" in the unified inbox (ALL_MAIL)', () => {
    renderBar('ALL_MAIL');
    expect(screen.getByText(ARCHIVE_LABEL)).toBeInTheDocument();
    expect(screen.queryByText(UNARCHIVE_LABEL)).not.toBeInTheDocument();
  });

  it('offers "Desarchivar" but not "Archivar" in the Archive box (ARCHIVE)', () => {
    renderBar('ARCHIVE');
    expect(screen.getByText(UNARCHIVE_LABEL)).toBeInTheDocument();
    expect(screen.queryByText(ARCHIVE_LABEL)).not.toBeInTheDocument();
  });

  it.each<EmailBox>(['SENT', 'SPAM', 'TRASH'])('offers neither archive action in %s', (box) => {
    renderBar(box);
    expect(screen.queryByText(ARCHIVE_LABEL)).not.toBeInTheDocument();
    expect(screen.queryByText(UNARCHIVE_LABEL)).not.toBeInTheDocument();
  });

  it('fires onAction("archive") when the Archive button is clicked in ALL_MAIL', async () => {
    const onAction = renderBar('ALL_MAIL');
    const user = userEvent.setup();
    await user.click(screen.getByText(ARCHIVE_LABEL));
    expect(onAction).toHaveBeenCalledWith('archive' satisfies BulkAction);
  });

  it('fires onAction("unarchive") when the Unarchive button is clicked in ARCHIVE', async () => {
    const onAction = renderBar('ARCHIVE');
    const user = userEvent.setup();
    await user.click(screen.getByText(UNARCHIVE_LABEL));
    expect(onAction).toHaveBeenCalledWith('unarchive' satisfies BulkAction);
  });
});
