// Unit tests for the pure rich-text body helpers (no DOM, no ProseMirror).
//
// These live in ``lib/`` precisely because both ``RichTextEditor`` (a
// ``components/ui/`` component) AND ``useComposerForm`` (a feature hook) need
// them, and a feature hook may not import from ``components/ui/``. They are
// pure string functions, so this is the canonical place to test them
// (test/CLAUDE.md §2.1: pure logic → unit, no mocks).

import { describe, expect, it } from 'vitest';

import { htmlIsEmpty, normalizeEmpty } from './richText';

describe('normalizeEmpty', () => {
  it('collapses an empty paragraph to an empty string', () => {
    expect(normalizeEmpty('<p></p>')).toBe('');
  });

  it('collapses an empty paragraph plus a stray break to an empty string', () => {
    expect(normalizeEmpty('<p></p><br>')).toBe('');
  });

  it('preserves real content verbatim', () => {
    expect(normalizeEmpty('<p>hola</p>')).toBe('<p>hola</p>');
  });

  it('preserves formatted content verbatim', () => {
    const html = '<p>a <strong>b</strong></p><ul><li>x</li></ul>';
    expect(normalizeEmpty(html)).toBe(html);
  });
});

describe('htmlIsEmpty', () => {
  it('is true for an empty paragraph', () => {
    expect(htmlIsEmpty('<p></p>')).toBe(true);
  });

  it('is true for a paragraph holding only a non-breaking space', () => {
    expect(htmlIsEmpty('<p>&nbsp;</p>')).toBe(true);
  });

  it('is false when there is visible text', () => {
    expect(htmlIsEmpty('<p>x</p>')).toBe(false);
  });

  it('is false for a list with items', () => {
    expect(htmlIsEmpty('<ul><li>a</li></ul>')).toBe(false);
  });
});
