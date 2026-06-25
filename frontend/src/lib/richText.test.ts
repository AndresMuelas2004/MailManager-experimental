// Unit tests for the pure rich-text body helpers (no DOM, no ProseMirror).
//
// These live in ``lib/`` precisely because both ``RichTextEditor`` (a
// ``components/ui/`` component) AND ``useComposerForm`` (a feature hook) need
// them, and a feature hook may not import from ``components/ui/``. They are
// pure string functions, so this is the canonical place to test them
// (test/CLAUDE.md §2.1: pure logic → unit, no mocks).

import { describe, expect, it } from 'vitest';

import { composeBodyWithSignature, htmlIsEmpty, normalizeEmpty } from './richText';

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

describe('composeBodyWithSignature', () => {
  const SIG = '<p>Jane Doe</p>';

  it('returns the existing body unchanged when the signature is null', () => {
    expect(composeBodyWithSignature('<p>quote</p>', null)).toBe('<p>quote</p>');
  });

  it('returns the existing body unchanged when the signature is undefined', () => {
    expect(composeBodyWithSignature('', undefined)).toBe('');
  });

  it('returns the existing body unchanged when the signature is visually empty', () => {
    // A lone ``<p></p>`` / ``<br>`` is "no signature" — nothing is prepended.
    expect(composeBodyWithSignature('<p>quote</p>', '<p></p>')).toBe('<p>quote</p>');
    expect(composeBodyWithSignature('<p>quote</p>', '   ')).toBe('<p>quote</p>');
  });

  it('prepends an empty paragraph + signature for a brand-new email (empty body)', () => {
    expect(composeBodyWithSignature('', SIG)).toBe(`<p></p>${SIG}`);
  });

  it('places the signature ABOVE the quoted reply/forward body', () => {
    const quote =
      '<p>El 23 de mayo, Ana escribió:</p>' +
      '<blockquote style="border-left:2px solid #ccc"><p>Hello</p></blockquote>';
    const result = composeBodyWithSignature(quote, SIG);
    expect(result).toBe(`<p></p>${SIG}${quote}`);
    // The signature comes before the blockquote, and the quote is left intact.
    expect(result.indexOf(SIG)).toBeLessThan(result.indexOf('<blockquote'));
    expect(result).toContain(quote);
  });
});
