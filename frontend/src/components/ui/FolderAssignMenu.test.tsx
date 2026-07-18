import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import FolderAssignMenu from './FolderAssignMenu';
import { renderWithProviders } from '../../test/renderWithProviders';
import type { FolderRef } from '../../api/types/dto';

const folders: FolderRef[] = [
  { folder_id: 'f1', name: 'Universidad', color: '#ff0000' },
  { folder_id: 'f2', name: 'Facturas', color: null },
];

describe('FolderAssignMenu', () => {
  it('opens the menu on click and reflects the assigned set', async () => {
    const user = userEvent.setup();
    renderWithProviders(
      <FolderAssignMenu
        folders={folders}
        assignedIds={new Set(['f1'])}
        onAssign={vi.fn()}
        onUnassign={vi.fn()}
      />,
    );
    // Closed initially.
    expect(screen.queryByRole('menu')).toBeNull();

    await user.click(screen.getByRole('button'));
    expect(screen.getByRole('menu')).toBeInTheDocument();

    // The assigned folder is checked, the other is not.
    expect(screen.getByRole('menuitemcheckbox', { name: /Universidad/ })).toHaveAttribute(
      'aria-checked',
      'true',
    );
    expect(screen.getByRole('menuitemcheckbox', { name: /Facturas/ })).toHaveAttribute(
      'aria-checked',
      'false',
    );
  });

  it('calls onAssign for an unassigned folder and onUnassign for an assigned one', async () => {
    const user = userEvent.setup();
    const onAssign = vi.fn();
    const onUnassign = vi.fn();
    renderWithProviders(
      <FolderAssignMenu
        folders={folders}
        assignedIds={new Set(['f1'])}
        onAssign={onAssign}
        onUnassign={onUnassign}
      />,
    );
    await user.click(screen.getByRole('button'));

    await user.click(screen.getByRole('menuitemcheckbox', { name: /Facturas/ }));
    expect(onAssign).toHaveBeenCalledWith('f2');

    await user.click(screen.getByRole('menuitemcheckbox', { name: /Universidad/ }));
    expect(onUnassign).toHaveBeenCalledWith('f1');
  });

  it('shows the empty state when there are no folders', async () => {
    const user = userEvent.setup();
    renderWithProviders(
      <FolderAssignMenu
        folders={[]}
        assignedIds={new Set()}
        onAssign={vi.fn()}
        onUnassign={vi.fn()}
      />,
    );
    await user.click(screen.getByRole('button'));
    expect(screen.getByRole('menu')).toBeInTheDocument();
    expect(screen.queryAllByRole('menuitemcheckbox')).toHaveLength(0);
  });

  it('stops click propagation so opening the menu never triggers the row handler', async () => {
    const user = userEvent.setup();
    const rowClick = vi.fn();
    renderWithProviders(
      <div onClick={rowClick}>
        <FolderAssignMenu
          folders={folders}
          assignedIds={new Set()}
          onAssign={vi.fn()}
          onUnassign={vi.fn()}
        />
      </div>,
    );
    await user.click(screen.getByRole('button'));
    // The menu (row) container's handler must not fire — the component stops
    // propagation so opening the menu on a listing row does not open the viewer.
    expect(rowClick).not.toHaveBeenCalled();
  });
});
