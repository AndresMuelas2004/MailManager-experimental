import { describe, expect, it } from 'vitest';

import { parsePageParam } from './pagination';

// Helper: build the URLSearchParams the page reads from. ``undefined``
// means the ``page`` key is absent entirely (the common first-load case).
function params(page?: string): URLSearchParams {
  const sp = new URLSearchParams();
  if (page !== undefined) sp.set('page', page);
  return sp;
}

describe('parsePageParam', () => {
  it('defaults to page 1 when the param is absent', () => {
    expect(parsePageParam(params())).toBe(1);
  });

  it('returns the parsed value for a valid page above 1', () => {
    expect(parsePageParam(params('3'))).toBe(3);
  });

  it('returns 1 for an explicit page=1', () => {
    expect(parsePageParam(params('1'))).toBe(1);
  });

  it('clamps zero up to 1', () => {
    // parseInt('0') is 0, which is falsy → the `|| 1` branch yields 1.
    expect(parsePageParam(params('0'))).toBe(1);
  });

  it('clamps a negative page up to 1', () => {
    // parseInt('-3') is -3 (truthy), so Math.max(1, -3) does the clamping.
    expect(parsePageParam(params('-3'))).toBe(1);
  });

  it('falls back to 1 for a non-numeric page', () => {
    // parseInt('abc') is NaN → NaN || 1 → 1.
    expect(parsePageParam(params('abc'))).toBe(1);
  });

  it('falls back to 1 for an empty page value', () => {
    // ?page= sets the key to '' (not null), so the ?? does not fire;
    // parseInt('', 10) is NaN and the `|| 1` rescues it.
    expect(parsePageParam(params(''))).toBe(1);
  });

  it('parses the leading integer of a value with trailing junk', () => {
    // parseInt('5abc', 10) stops at the first non-digit → 5.
    expect(parsePageParam(params('5abc'))).toBe(5);
  });

  it('truncates a fractional page to its integer part', () => {
    expect(parsePageParam(params('2.9'))).toBe(2);
  });
});
