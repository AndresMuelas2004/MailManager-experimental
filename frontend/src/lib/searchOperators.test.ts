import { describe, expect, it } from 'vitest';

import { parseInOperator } from './searchOperators';

describe('parseInOperator', () => {
  it('maps every supported in: value to its box', () => {
    expect(parseInOperator('in:sent')).toBe('SENT');
    expect(parseInOperator('in:inbox')).toBe('ALL_MAIL');
    expect(parseInOperator('in:allmail')).toBe('ALL_MAIL');
    expect(parseInOperator('in:trash')).toBe('TRASH');
    expect(parseInOperator('in:spam')).toBe('SPAM');
    expect(parseInOperator('in:archive')).toBe('ARCHIVE');
  });

  it('accepts a double-quoted value', () => {
    expect(parseInOperator('in:"sent"')).toBe('SENT');
  });

  it('is case-insensitive on both the operator and the value', () => {
    expect(parseInOperator('IN:SENT')).toBe('SENT');
    expect(parseInOperator('In:Trash')).toBe('TRASH');
  });

  it('returns null for an unsupported value', () => {
    expect(parseInOperator('in:archivados')).toBeNull();
  });

  it('returns null when there is no in: operator', () => {
    expect(parseInOperator('from:linkedin oferta')).toBeNull();
  });

  it('returns null for an empty string', () => {
    expect(parseInOperator('')).toBeNull();
  });

  it('keeps the last valid in: when several are present', () => {
    expect(parseInOperator('in:inbox in:trash')).toBe('TRASH');
  });

  it('does not let a later invalid value override an earlier valid one', () => {
    expect(parseInOperator('in:sent in:archivados')).toBe('SENT');
  });

  it('iterates every token separated by a single space and the last valid wins', () => {
    expect(parseInOperator('in:inbox in:sent in:trash')).toBe('TRASH');
  });

  // The (?:^|\s) anchor is the load-bearing part of the regex: in: must be
  // a real token, not a substring of another operator's value.
  it('does not fire inside "from:linkedin" (the "in" of linkedin)', () => {
    expect(parseInOperator('from:linkedin')).toBeNull();
  });

  it('does not fire inside "subject:internal"', () => {
    expect(parseInOperator('subject:internal')).toBeNull();
  });

  it('does not fire inside "cousin:bob" (in: preceded by a letter, not a boundary)', () => {
    expect(parseInOperator('cousin:bob')).toBeNull();
  });

  it('matches an in: surrounded by spaces among other operators and free text', () => {
    expect(parseInOperator('from:x in:sent hola')).toBe('SENT');
  });
});
