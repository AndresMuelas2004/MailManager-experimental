import { useEffect, useId, useMemo, useRef, useState } from 'react';

import type { ContactSuggestion } from '../../api/types/dto';

type Props = {
  label: string; // "Para" | "CC" | "BCC"
  value: string; // full field string (comma-separated)
  onChange: (next: string) => void;
  placeholder?: string;
  suggestions: ContactSuggestion[];
  loading: boolean;
  // Notifies the active fragment (text after the last comma) to the host,
  // which debounces it and drives the data hook. Emitted on every keystroke,
  // even when shorter than the gating threshold or empty.
  onQueryChange: (fragment: string) => void;
};

const MIN_FRAGMENT_LENGTH = 2;

// The active fragment is what the user is currently typing: everything
// after the last comma, trimmed. ``activeFragment('')`` returns '' safely.
function activeFragment(value: string): string {
  const lastComma = value.lastIndexOf(',');
  return (lastComma === -1 ? value : value.slice(lastComma + 1)).trim();
}

// Replace the active fragment with the chosen email and append ", " so the
// user can keep typing the next recipient. Only the email is inserted.
function applySelection(value: string, email: string): string {
  const lastComma = value.lastIndexOf(',');
  const head = lastComma === -1 ? '' : value.slice(0, lastComma + 1);
  const prefix = head ? head.trimEnd() + ' ' : '';
  return `${prefix}${email}, `;
}

// Addresses already committed in the field = the tokens BEFORE the last
// comma (parsed exactly like ``parseRecipientsImpl``: split / trim / filter /
// lowercase). The active fragment (after the last comma) is intentionally
// excluded — it is what the user is matching against. Replicated locally
// because ``components/ui/`` may not import from ``features/``.
function committedAddresses(value: string): Set<string> {
  const lastComma = value.lastIndexOf(',');
  if (lastComma === -1) return new Set();
  return new Set(
    value
      .slice(0, lastComma)
      .split(',')
      .map((r) => r.trim().toLowerCase())
      .filter(Boolean),
  );
}

export default function RecipientAutocompleteInput({
  label,
  value,
  onChange,
  placeholder,
  suggestions,
  loading,
  onQueryChange,
}: Props) {
  const [open, setOpen] = useState(false);
  const [highlightedIndex, setHighlightedIndex] = useState(-1);
  const containerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const listboxId = useId();

  const fragment = activeFragment(value);
  const fragmentLongEnough = fragment.length >= MIN_FRAGMENT_LENGTH;

  const filtered = useMemo(() => {
    const excluded = committedAddresses(value);
    return suggestions.filter((s) => !excluded.has(s.email.toLowerCase()));
  }, [suggestions, value]);

  // Close on outside click / Escape — listeners are registered ONLY while
  // ``open`` so a closed dropdown installs nothing on ``document`` (keeps the
  // composer's other interactions untouched). Same pattern as ProviderSelect /
  // Sidebar.
  useEffect(() => {
    if (!open) return;

    function handleClickOutside(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }

    document.addEventListener('mousedown', handleClickOutside);
    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
    };
  }, [open]);

  // The dropdown is visible only while this field is the one being edited
  // (``open``), the fragment is long enough, and there is something to show
  // (results, or a transient loading / empty notice).
  const showDropdown = open && fragmentLongEnough && (loading || filtered.length > 0);
  const showEmptyNotice = open && fragmentLongEnough && !loading && filtered.length === 0;

  function handleInputChange(e: React.ChangeEvent<HTMLInputElement>) {
    const next = e.target.value;
    onChange(next);
    // Always emit the fragment, even when short / empty: the hook owns the
    // gating, so dropping below 2 chars must reach the host to reset to [].
    onQueryChange(activeFragment(next));
    setOpen(true);
    setHighlightedIndex(-1);
  }

  function selectSuggestion(suggestion: ContactSuggestion) {
    onChange(applySelection(value, suggestion.email));
    onQueryChange('');
    setOpen(false);
    setHighlightedIndex(-1);
    // Return focus so the user can keep typing the next recipient.
    inputRef.current?.focus();
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === 'ArrowDown') {
      if (!showDropdown || filtered.length === 0) return;
      e.preventDefault();
      setHighlightedIndex((i) => (i + 1) % filtered.length);
      return;
    }
    if (e.key === 'ArrowUp') {
      if (!showDropdown || filtered.length === 0) return;
      e.preventDefault();
      setHighlightedIndex((i) => (i <= 0 ? filtered.length - 1 : i - 1));
      return;
    }
    if (e.key === 'Enter') {
      if (showDropdown && highlightedIndex >= 0 && highlightedIndex < filtered.length) {
        e.preventDefault();
        selectSuggestion(filtered[highlightedIndex]);
      }
      return;
    }
    if (e.key === 'Escape') {
      if (open) {
        e.preventDefault();
        e.stopPropagation();
        setOpen(false);
        setHighlightedIndex(-1);
      }
      return;
    }
    if (e.key === 'Tab') {
      setOpen(false);
      setHighlightedIndex(-1);
    }
  }

  const inputBaseClass =
    'h-10 rounded-[10px] border-[1.5px] border-zinc-200 px-3 text-sm text-zinc-900 placeholder:text-zinc-400 focus:border-blue-600 focus:outline-none';

  const activeOptionId =
    showDropdown && highlightedIndex >= 0 ? `${listboxId}-opt-${highlightedIndex}` : undefined;

  return (
    <div ref={containerRef} className="relative flex flex-col gap-1.5">
      <label className="text-sm font-medium text-zinc-900">{label}</label>
      <input
        ref={inputRef}
        type="text"
        value={value}
        onChange={handleInputChange}
        onKeyDown={handleKeyDown}
        onFocus={() => setOpen(true)}
        placeholder={placeholder}
        className={inputBaseClass}
        role="combobox"
        aria-expanded={showDropdown}
        aria-autocomplete="list"
        aria-controls={listboxId}
        aria-activedescendant={activeOptionId}
      />

      {showDropdown && (
        <ul
          id={listboxId}
          role="listbox"
          className="absolute top-full left-0 z-20 mt-1 max-h-56 w-full overflow-auto rounded-[10px] border border-zinc-200 bg-white py-1 shadow-lg"
        >
          {filtered.length === 0 && loading ? (
            <li className="px-3 py-2 text-sm text-zinc-400">Buscando…</li>
          ) : (
            filtered.map((s, index) => (
              <li key={s.email} role="presentation">
                <button
                  id={`${listboxId}-opt-${index}`}
                  type="button"
                  role="option"
                  aria-selected={index === highlightedIndex}
                  // ``onMouseDown`` (not ``onClick``) so the selection runs
                  // before the input's blur / the document mousedown handler
                  // closes the dropdown and swallows the click.
                  onMouseDown={(e) => {
                    e.preventDefault();
                    selectSuggestion(s);
                  }}
                  onMouseEnter={() => setHighlightedIndex(index)}
                  className={`flex w-full flex-col gap-0.5 px-3 py-2 text-left text-sm ${
                    index === highlightedIndex ? 'bg-zinc-100' : 'hover:bg-zinc-50'
                  }`}
                >
                  {s.name ? (
                    <>
                      <span className="text-zinc-900">{s.name}</span>
                      <span className="text-xs text-zinc-500">{s.email}</span>
                    </>
                  ) : (
                    <span className="text-zinc-900">{s.email}</span>
                  )}
                </button>
              </li>
            ))
          )}
        </ul>
      )}

      {showEmptyNotice && (
        <div className="absolute top-full left-0 z-20 mt-1 w-full rounded-[10px] border border-zinc-200 bg-white px-3 py-2 text-sm text-zinc-400 shadow-lg">
          Sin sugerencias
        </div>
      )}
    </div>
  );
}
