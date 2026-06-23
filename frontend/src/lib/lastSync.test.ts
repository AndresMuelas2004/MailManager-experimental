/**
 * Unit tests for the ``lastSync`` localStorage helpers.
 *
 * Pure-ish: the only impurity is ``window.localStorage``, exercised directly in
 * jsdom (the same approach as ``lib/i18n/detect.test.ts``). The storage-failure
 * branches are forced with a narrow ``vi.spyOn`` on the storage methods — a
 * sanctioned spy on a browser API to reach a defensive path, not a mock of the
 * function under test.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { readLastSyncedAt, writeLastSyncedAt } from './lastSync';

describe('lastSync', () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  afterEach(() => {
    window.localStorage.clear();
    vi.restoreAllMocks();
  });

  it('round-trips a written epoch-ms value back through read', () => {
    writeLastSyncedAt('emails:mb_1:ALL', 1_717_243_200_000);
    expect(readLastSyncedAt('emails:mb_1:ALL')).toBe(1_717_243_200_000);
  });

  it('namespaces by scope key — an unrelated scope is unaffected', () => {
    writeLastSyncedAt('emails:mb_1:ALL', 111);
    expect(readLastSyncedAt('vmbox:vmb_1')).toBeNull();
  });

  it('returns null when the scope key is absent', () => {
    expect(readLastSyncedAt('emails:missing:ALL')).toBeNull();
  });

  it('returns null when the stored value is not a finite number', () => {
    // Write a non-numeric payload under the prefixed key the reader expects.
    window.localStorage.setItem('lastSync:emails:mb_1:ALL', 'not-a-number');
    expect(readLastSyncedAt('emails:mb_1:ALL')).toBeNull();
  });

  it('returns null when localStorage.getItem throws (private mode / SSR)', () => {
    vi.spyOn(window.localStorage, 'getItem').mockImplementation(() => {
      throw new Error('storage unavailable');
    });
    expect(readLastSyncedAt('emails:mb_1:ALL')).toBeNull();
  });

  it('swallows a localStorage.setItem failure instead of propagating it', () => {
    vi.spyOn(window.localStorage, 'setItem').mockImplementation(() => {
      throw new Error('quota exceeded');
    });
    expect(() => writeLastSyncedAt('emails:mb_1:ALL', 123)).not.toThrow();
  });
});
