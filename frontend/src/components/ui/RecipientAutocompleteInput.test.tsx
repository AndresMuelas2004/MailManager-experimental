/**
 * Component tests for ``RecipientAutocompleteInput``.
 *
 * The component is presentational and self-contained (no providers, no
 * HTTP) — it receives ``suggestions`` / ``loading`` by props and emits
 * events. Inputs are located by label / placeholder rather than by role:
 * the input carries ``role="combobox"`` for a11y, so a ``getByRole('textbox')``
 * lookup would not find it. Anchoring on visible text keeps the selectors
 * robust regardless of that decision.
 */

import { useState } from 'react';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import RecipientAutocompleteInput from './RecipientAutocompleteInput';
import { I18nProvider } from '../../lib/i18n';
import { pinTestLang } from '../../test/i18nTestLang';
import type { ContactSuggestion } from '../../api/types/dto';

// The component reads its empty/loading copy through ``t()`` (useTranslation),
// so the harness wraps it in the real I18nProvider; Spanish is pinned for
// determinism even though these tests assert on prop-driven text.
pinTestLang('es');

const SUGGESTIONS: ContactSuggestion[] = [
  { email: 'amparo@ejemplo.com', name: 'Amparo López' },
  { email: 'soporte@empresa.com', name: null },
];

type HarnessProps = {
  initialValue?: string;
  suggestions?: ContactSuggestion[];
  loading?: boolean;
  onChangeSpy?: (v: string) => void;
  onQueryChangeSpy?: (f: string) => void;
};

// Controlled wrapper so the input's value actually updates as the user
// types / selects — the component is controlled, a static prop would not
// reflect keystrokes.
function Harness({
  initialValue = '',
  suggestions = SUGGESTIONS,
  loading = false,
  onChangeSpy,
  onQueryChangeSpy,
}: HarnessProps) {
  const [value, setValue] = useState(initialValue);
  return (
    <I18nProvider>
      <RecipientAutocompleteInput
        label="Para"
        value={value}
        onChange={(next) => {
          setValue(next);
          onChangeSpy?.(next);
        }}
        placeholder="correo@ejemplo.com"
        suggestions={suggestions}
        loading={loading}
        onQueryChange={(f) => onQueryChangeSpy?.(f)}
      />
    </I18nProvider>
  );
}

describe('RecipientAutocompleteInput', () => {
  it('renders the controlled value and the label', () => {
    render(<Harness initialValue="hola@ejemplo.com" />);
    expect(screen.getByText('Para')).toBeInTheDocument();
    expect(screen.getByDisplayValue('hola@ejemplo.com')).toBeInTheDocument();
  });

  it('emits onChange and onQueryChange with the fragment after the last comma', async () => {
    const onChangeSpy = vi.fn();
    const onQueryChangeSpy = vi.fn();
    const user = userEvent.setup();
    render(
      <Harness
        initialValue="first@ejemplo.com, "
        onChangeSpy={onChangeSpy}
        onQueryChangeSpy={onQueryChangeSpy}
      />,
    );

    const input = screen.getByPlaceholderText('correo@ejemplo.com');
    await user.click(input);
    await user.type(input, 'am');

    // The fragment notified to the host is just the text after the last comma.
    expect(onQueryChangeSpy).toHaveBeenLastCalledWith('am');
    expect(onChangeSpy).toHaveBeenLastCalledWith('first@ejemplo.com, am');
  });

  it('shows the dropdown with each suggestion once the fragment is >= 2 chars', async () => {
    const user = userEvent.setup();
    render(<Harness />);

    const input = screen.getByPlaceholderText('correo@ejemplo.com');
    await user.click(input);
    await user.type(input, 'am');

    const listbox = screen.getByRole('listbox');
    expect(within(listbox).getByText('Amparo López')).toBeInTheDocument();
    expect(within(listbox).getByText('amparo@ejemplo.com')).toBeInTheDocument();
    // The nameless suggestion shows only its address.
    expect(within(listbox).getByText('soporte@empresa.com')).toBeInTheDocument();
  });

  it('does not open the dropdown while the fragment is shorter than 2 chars', async () => {
    const user = userEvent.setup();
    render(<Harness />);

    const input = screen.getByPlaceholderText('correo@ejemplo.com');
    await user.click(input);
    await user.type(input, 'a');

    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
  });

  it('inserts only the email plus ", " when an item is clicked and closes the dropdown', async () => {
    const onChangeSpy = vi.fn();
    const user = userEvent.setup();
    render(<Harness onChangeSpy={onChangeSpy} />);

    const input = screen.getByPlaceholderText('correo@ejemplo.com');
    await user.click(input);
    await user.type(input, 'am');

    await user.click(screen.getByText('Amparo López'));

    expect(onChangeSpy).toHaveBeenLastCalledWith('amparo@ejemplo.com, ');
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
  });

  it('navigates with ArrowDown and selects the highlighted item with Enter', async () => {
    const onChangeSpy = vi.fn();
    const user = userEvent.setup();
    render(<Harness onChangeSpy={onChangeSpy} />);

    const input = screen.getByPlaceholderText('correo@ejemplo.com');
    await user.click(input);
    await user.type(input, 'so');

    // Only the second suggestion contains "so"; but the component does not
    // filter by text (the backend does) — both are present. ArrowDown twice
    // lands on the second item, Enter selects it.
    await user.keyboard('{ArrowDown}{ArrowDown}');
    await user.keyboard('{Enter}');

    expect(onChangeSpy).toHaveBeenLastCalledWith('soporte@empresa.com, ');
  });

  it('closes the dropdown on Escape without changing the value', async () => {
    const onChangeSpy = vi.fn();
    const user = userEvent.setup();
    render(<Harness onChangeSpy={onChangeSpy} />);

    const input = screen.getByPlaceholderText('correo@ejemplo.com');
    await user.click(input);
    await user.type(input, 'am');
    expect(screen.getByRole('listbox')).toBeInTheDocument();

    const changeCallsBeforeEscape = onChangeSpy.mock.calls.length;
    await user.keyboard('{Escape}');

    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
    // Escape only closes — it does not edit the field.
    expect(onChangeSpy.mock.calls.length).toBe(changeCallsBeforeEscape);
  });

  it('excludes a suggestion whose email is already committed in the field', async () => {
    const user = userEvent.setup();
    render(<Harness initialValue="amparo@ejemplo.com, " />);

    const input = screen.getByPlaceholderText('correo@ejemplo.com');
    await user.click(input);
    await user.type(input, 'so');

    const listbox = screen.getByRole('listbox');
    // ``amparo@ejemplo.com`` is already committed (before the last comma) →
    // filtered out. The other suggestion still shows.
    expect(within(listbox).queryByText('Amparo López')).not.toBeInTheDocument();
    expect(within(listbox).getByText('soporte@empresa.com')).toBeInTheDocument();
  });

  it('does not break manual typing of a full address and comma when nothing is selected', async () => {
    const onChangeSpy = vi.fn();
    const user = userEvent.setup();
    render(<Harness suggestions={[]} onChangeSpy={onChangeSpy} />);

    const input = screen.getByPlaceholderText('correo@ejemplo.com');
    await user.click(input);
    await user.type(input, 'manual@ejemplo.com, ');

    expect(onChangeSpy).toHaveBeenLastCalledWith('manual@ejemplo.com, ');
    // No suggestions → no dropdown, manual entry behaves exactly as before.
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
  });
});
