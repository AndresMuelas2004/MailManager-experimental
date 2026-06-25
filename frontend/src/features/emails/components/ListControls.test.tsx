import { render as rtlRender, screen, type RenderOptions } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactElement } from 'react';
import { describe, expect, it, vi } from 'vitest';

import ListControls from './ListControls';
import { DEFAULT_LIST_CONTROLS } from '../../../lib/listControls';
import { I18nProvider } from '../../../lib/i18n';
import { pinTestLang } from '../../../test/i18nTestLang';

// ListControls renders its labels/chips through ``t()``, so it must mount
// inside the real I18nProvider; Spanish is pinned so the assertions read the
// Spanish copy (matching the other component tests in this folder).
pinTestLang('es');

function render(ui: ReactElement, options?: Omit<RenderOptions, 'wrapper'>) {
  return rtlRender(ui, { wrapper: I18nProvider, ...options });
}

describe('ListControls', () => {
  it('renders the sort select with the three options and the current value', () => {
    render(<ListControls value={DEFAULT_LIST_CONTROLS} onChange={() => {}} />);
    const select = screen.getByRole('combobox');
    expect(select).toHaveValue('date');
    // All three sort options are present (Spanish copy).
    expect(screen.getByRole('option', { name: 'Fecha' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'Remitente' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'Asunto' })).toBeInTheDocument();
  });

  it('reflects the active state of each chip via aria-pressed', () => {
    render(
      <ListControls
        value={{ ...DEFAULT_LIST_CONTROLS, unread: true, hasAttachment: false, favorite: true }}
        onChange={() => {}}
      />,
    );
    expect(screen.getByRole('button', { name: 'No leídos' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    expect(screen.getByRole('button', { name: 'Con adjuntos' })).toHaveAttribute(
      'aria-pressed',
      'false',
    );
    expect(screen.getByRole('button', { name: 'Destacados' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
  });

  it('emits onChange with the chosen sort key when the select changes', async () => {
    const onChange = vi.fn();
    render(<ListControls value={DEFAULT_LIST_CONTROLS} onChange={onChange} />);
    await userEvent.selectOptions(screen.getByRole('combobox'), 'subject');
    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onChange).toHaveBeenCalledWith({ ...DEFAULT_LIST_CONTROLS, sort: 'subject' });
  });

  it('toggles the direction from desc to asc when the direction button is clicked', async () => {
    const onChange = vi.fn();
    render(<ListControls value={DEFAULT_LIST_CONTROLS} onChange={onChange} />);
    // Default dir is desc → the button is labelled "Descendente".
    await userEvent.click(screen.getByRole('button', { name: 'Descendente' }));
    expect(onChange).toHaveBeenCalledWith({ ...DEFAULT_LIST_CONTROLS, dir: 'asc' });
  });

  it('toggles the direction back from asc to desc', async () => {
    const onChange = vi.fn();
    render(<ListControls value={{ ...DEFAULT_LIST_CONTROLS, dir: 'asc' }} onChange={onChange} />);
    await userEvent.click(screen.getByRole('button', { name: 'Ascendente' }));
    expect(onChange).toHaveBeenCalledWith({ ...DEFAULT_LIST_CONTROLS, dir: 'desc' });
  });

  it('toggles a chip on when clicked from its inactive state', async () => {
    const onChange = vi.fn();
    render(<ListControls value={DEFAULT_LIST_CONTROLS} onChange={onChange} />);
    await userEvent.click(screen.getByRole('button', { name: 'No leídos' }));
    expect(onChange).toHaveBeenCalledWith({ ...DEFAULT_LIST_CONTROLS, unread: true });
  });

  it('toggles a chip off when clicked from its active state', async () => {
    const onChange = vi.fn();
    render(
      <ListControls value={{ ...DEFAULT_LIST_CONTROLS, favorite: true }} onChange={onChange} />,
    );
    await userEvent.click(screen.getByRole('button', { name: 'Destacados' }));
    expect(onChange).toHaveBeenCalledWith({ ...DEFAULT_LIST_CONTROLS, favorite: false });
  });

  it('emits each chip toggle independently of the others', async () => {
    const onChange = vi.fn();
    render(<ListControls value={DEFAULT_LIST_CONTROLS} onChange={onChange} />);
    await userEvent.click(screen.getByRole('button', { name: 'Con adjuntos' }));
    // Only hasAttachment flips; unread and favorite stay at their defaults.
    expect(onChange).toHaveBeenCalledWith({ ...DEFAULT_LIST_CONTROLS, hasAttachment: true });
  });
});
