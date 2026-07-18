import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import FolderChips from './FolderChips';
import type { FolderRef } from '../../api/types/dto';

const folders: FolderRef[] = [
  { folder_id: 'f1', name: 'Universidad', color: '#ff0000' },
  { folder_id: 'f2', name: 'Facturas', color: null },
];

describe('FolderChips', () => {
  it('renders nothing when there are no folders', () => {
    // Returns null so every EmailTable / viewer mount can drop it in
    // unconditionally without an empty wrapper.
    const { container } = render(<FolderChips folders={[]} />);
    expect(container.firstChild).toBeNull();
  });

  it('renders one chip per folder with its name', () => {
    render(<FolderChips folders={folders} />);
    expect(screen.getByText('Universidad')).toBeInTheDocument();
    expect(screen.getByText('Facturas')).toBeInTheDocument();
  });
});
