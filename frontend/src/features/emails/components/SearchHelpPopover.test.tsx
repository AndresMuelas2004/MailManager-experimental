import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import SearchHelpPopover from './SearchHelpPopover';

// SearchHelpPopover is a presentational, self-contained widget: it owns its
// open/closed UI state and its dismissal listeners. No MSW, no router, no
// query client — a plain render is enough. The trigger is a real <button>
// with an aria-label, so it never collides with the searchbox queries of
// the page-level tests.

describe('SearchHelpPopover', () => {
  it('does not show the panel until the trigger is clicked', () => {
    render(<SearchHelpPopover />);
    const trigger = screen.getByRole('button', { name: 'Ayuda de búsqueda' });
    expect(trigger).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByText('Operadores de búsqueda')).not.toBeInTheDocument();
  });

  it('opens the panel with the operator list when the trigger is clicked', async () => {
    const user = userEvent.setup();
    render(<SearchHelpPopover />);

    await user.click(screen.getByRole('button', { name: 'Ayuda de búsqueda' }));

    expect(screen.getByText('Operadores de búsqueda')).toBeInTheDocument();
    expect(screen.getByText('has:attachment')).toBeInTheDocument();
    // is:starred is documented as the alias of is:favorite, inside the row's
    // description text.
    expect(screen.getByText(/is:starred/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Ayuda de búsqueda' })).toHaveAttribute(
      'aria-expanded',
      'true',
    );
  });

  it('closes the panel when Escape is pressed', async () => {
    const user = userEvent.setup();
    render(<SearchHelpPopover />);

    await user.click(screen.getByRole('button', { name: 'Ayuda de búsqueda' }));
    expect(screen.getByText('Operadores de búsqueda')).toBeInTheDocument();

    await user.keyboard('{Escape}');

    expect(screen.queryByText('Operadores de búsqueda')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Ayuda de búsqueda' })).toHaveAttribute(
      'aria-expanded',
      'false',
    );
  });

  it('closes the panel when the user clicks outside it', async () => {
    const user = userEvent.setup();
    render(
      <div>
        <button type="button">outside</button>
        <SearchHelpPopover />
      </div>,
    );

    await user.click(screen.getByRole('button', { name: 'Ayuda de búsqueda' }));
    expect(screen.getByText('Operadores de búsqueda')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'outside' }));

    expect(screen.queryByText('Operadores de búsqueda')).not.toBeInTheDocument();
  });

  it('toggles the panel closed on a second trigger click', async () => {
    const user = userEvent.setup();
    render(<SearchHelpPopover />);
    const trigger = screen.getByRole('button', { name: 'Ayuda de búsqueda' });

    await user.click(trigger);
    expect(screen.getByText('Operadores de búsqueda')).toBeInTheDocument();

    await user.click(trigger);
    expect(screen.queryByText('Operadores de búsqueda')).not.toBeInTheDocument();
  });
});
