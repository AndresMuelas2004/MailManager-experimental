// Pure helpers for the listing's sort + quick-filter controls. Parses and
// serialises the control state from/to the URL search params, with stable
// types and defaults. Leaf layer (no imports from the rest of ``src/``),
// like ``pagination.ts`` / ``searchOperators.ts``.

export type SortKey = 'date' | 'sender' | 'subject';
export type SortDir = 'asc' | 'desc';

export type ListControlsState = {
  sort: SortKey;
  dir: SortDir;
  unread: boolean;
  hasAttachment: boolean;
  favorite: boolean; // chip "Starred"
};

export const DEFAULT_LIST_CONTROLS: ListControlsState = {
  sort: 'date',
  dir: 'desc',
  unread: false,
  hasAttachment: false,
  favorite: false,
};

const SORT_KEYS: readonly SortKey[] = ['date', 'sender', 'subject'];

// URL keys owned by the sort + quick-filter controls (see docs/limits §3).
const CONTROL_URL_KEYS = ['sort', 'dir', 'unread', 'attachment', 'favorite'] as const;

// Build the ``search`` a box/account navigation link should carry from the
// current one: drops the sort/filter controls and ``page`` so switching context
// resets the controls to their defaults (ordenar-y-filtrar §4.7) and pagination
// to page 1 (listado §7.1), while preserving the rest of the query — notably
// the active lupa ``q`` term (buzones-y-vista-unificada §2.3). Returned without
// the leading "?".
export function navSearchResettingControls(search: string): string {
  const params = new URLSearchParams(search);
  for (const key of CONTROL_URL_KEYS) params.delete(key);
  params.delete('page');
  return params.toString();
}

// Parse from the URL search params. Unknown/missing values fall back to the
// defaults so a hand-typed or stale URL never breaks the listing.
export function parseListControls(params: URLSearchParams): ListControlsState {
  const sortRaw = params.get('sort');
  const sort: SortKey = (SORT_KEYS as readonly string[]).includes(sortRaw ?? '')
    ? (sortRaw as SortKey)
    : 'date';
  const dir: SortDir = params.get('dir') === 'asc' ? 'asc' : 'desc';
  return {
    sort,
    dir,
    unread: params.get('unread') === '1',
    hasAttachment: params.get('attachment') === '1',
    favorite: params.get('favorite') === '1',
  };
}

// Whether the current state differs from the defaults (used to decide the
// "filtered empty" message and whether any chip is active).
export function isAnyFilterActive(s: ListControlsState): boolean {
  return s.unread || s.hasAttachment || s.favorite;
}

// Mutate a URLSearchParams copy to reflect a new control state. Only writes
// non-default keys (clean URLs); ALWAYS deletes ``page`` (any control change
// resets to page 1, like the search box does).
export function writeListControls(params: URLSearchParams, s: ListControlsState): void {
  const setFlag = (key: string, on: boolean): void => {
    if (on) params.set(key, '1');
    else params.delete(key);
  };
  // sort
  if (s.sort === 'date') params.delete('sort');
  else params.set('sort', s.sort);
  // dir
  if (s.dir === 'asc') params.set('dir', 'asc');
  else params.delete('dir');
  // chips
  setFlag('unread', s.unread);
  setFlag('attachment', s.hasAttachment);
  setFlag('favorite', s.favorite);
  // any control change resets pagination
  params.delete('page');
}
