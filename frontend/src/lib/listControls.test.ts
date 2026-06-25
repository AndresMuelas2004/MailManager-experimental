import { describe, expect, it } from 'vitest';

import {
  DEFAULT_LIST_CONTROLS,
  isAnyFilterActive,
  parseListControls,
  writeListControls,
  type ListControlsState,
} from './listControls';

// Helper: build a URLSearchParams from a plain record. Absent keys mean the
// param is missing entirely (the common first-load case).
function params(entries: Record<string, string> = {}): URLSearchParams {
  return new URLSearchParams(entries);
}

describe('parseListControls', () => {
  it('returns the defaults when no params are present', () => {
    expect(parseListControls(params())).toEqual(DEFAULT_LIST_CONTROLS);
  });

  it('parses a valid sort value', () => {
    expect(parseListControls(params({ sort: 'subject' })).sort).toBe('subject');
    expect(parseListControls(params({ sort: 'sender' })).sort).toBe('sender');
  });

  it('falls back to date for an unknown sort value', () => {
    // A hand-typed or stale URL with junk must never break the listing.
    expect(parseListControls(params({ sort: 'size' })).sort).toBe('date');
  });

  it('parses dir=asc and defaults everything else to desc', () => {
    expect(parseListControls(params({ dir: 'asc' })).dir).toBe('asc');
    // Only the literal 'asc' yields asc; any other value is desc.
    expect(parseListControls(params({ dir: 'down' })).dir).toBe('desc');
    expect(parseListControls(params()).dir).toBe('desc');
  });

  it('reads the chip flags only when the value is exactly "1"', () => {
    const on = parseListControls(params({ unread: '1', attachment: '1', favorite: '1' }));
    expect(on.unread).toBe(true);
    expect(on.hasAttachment).toBe(true);
    expect(on.favorite).toBe(true);
  });

  it('treats any non-"1" chip value as off', () => {
    const off = parseListControls(params({ unread: 'true', attachment: '0', favorite: 'yes' }));
    expect(off.unread).toBe(false);
    expect(off.hasAttachment).toBe(false);
    expect(off.favorite).toBe(false);
  });

  it('maps the URL key "attachment" to the hasAttachment field', () => {
    // The wire/URL key is ``attachment`` but the state field is
    // ``hasAttachment`` — the mapping must not drift.
    expect(parseListControls(params({ attachment: '1' })).hasAttachment).toBe(true);
    expect(parseListControls(params({ hasAttachment: '1' })).hasAttachment).toBe(false);
  });
});

describe('isAnyFilterActive', () => {
  it('is false for the defaults', () => {
    expect(isAnyFilterActive(DEFAULT_LIST_CONTROLS)).toBe(false);
  });

  it('ignores sort/dir — only the chips count as an active filter', () => {
    const sortedOnly: ListControlsState = {
      ...DEFAULT_LIST_CONTROLS,
      sort: 'subject',
      dir: 'asc',
    };
    expect(isAnyFilterActive(sortedOnly)).toBe(false);
  });

  it.each([
    ['unread', { unread: true }],
    ['hasAttachment', { hasAttachment: true }],
    ['favorite', { favorite: true }],
  ] as const)('is true when the %s chip is on', (_label, override) => {
    expect(isAnyFilterActive({ ...DEFAULT_LIST_CONTROLS, ...override })).toBe(true);
  });
});

describe('writeListControls', () => {
  it('writes nothing for the defaults (clean URL)', () => {
    const sp = params();
    writeListControls(sp, DEFAULT_LIST_CONTROLS);
    // No sort/dir/chip keys are added when everything is default.
    expect(sp.has('sort')).toBe(false);
    expect(sp.has('dir')).toBe(false);
    expect(sp.has('unread')).toBe(false);
    expect(sp.has('attachment')).toBe(false);
    expect(sp.has('favorite')).toBe(false);
  });

  it('writes only the non-default sort and dir', () => {
    const sp = params();
    writeListControls(sp, { ...DEFAULT_LIST_CONTROLS, sort: 'sender', dir: 'asc' });
    expect(sp.get('sort')).toBe('sender');
    expect(sp.get('dir')).toBe('asc');
  });

  it('omits sort when it is the default date, and dir when it is desc', () => {
    const sp = params({ sort: 'subject', dir: 'asc' });
    // Switching back to the defaults must REMOVE the keys, not set them.
    writeListControls(sp, DEFAULT_LIST_CONTROLS);
    expect(sp.has('sort')).toBe(false);
    expect(sp.has('dir')).toBe(false);
  });

  it('writes active chips as "1" and deletes inactive ones', () => {
    const sp = params({ attachment: '1' });
    writeListControls(sp, { ...DEFAULT_LIST_CONTROLS, unread: true });
    expect(sp.get('unread')).toBe('1');
    // The previously-active attachment chip is now off → removed.
    expect(sp.has('attachment')).toBe(false);
  });

  it('always deletes the page param so a control change resets pagination', () => {
    const sp = params({ page: '4' });
    writeListControls(sp, { ...DEFAULT_LIST_CONTROLS, favorite: true });
    expect(sp.has('page')).toBe(false);
  });

  it('preserves unrelated params already on the URL', () => {
    const sp = params({ q: 'hello', box: 'SENT' });
    writeListControls(sp, { ...DEFAULT_LIST_CONTROLS, sort: 'subject' });
    expect(sp.get('q')).toBe('hello');
    expect(sp.get('box')).toBe('SENT');
  });
});

describe('parse ↔ write round-trip', () => {
  it.each<ListControlsState>([
    DEFAULT_LIST_CONTROLS,
    { sort: 'subject', dir: 'asc', unread: false, hasAttachment: false, favorite: false },
    { sort: 'sender', dir: 'desc', unread: true, hasAttachment: true, favorite: true },
    { sort: 'date', dir: 'asc', unread: true, hasAttachment: false, favorite: true },
  ])('writing then parsing yields the original state %#', (state) => {
    const sp = params();
    writeListControls(sp, state);
    expect(parseListControls(sp)).toEqual(state);
  });
});
