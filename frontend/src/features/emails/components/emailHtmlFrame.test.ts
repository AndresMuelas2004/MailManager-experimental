import { describe, expect, it } from 'vitest';

import { escapeHtml, hasRenderableBody, wrapHtmlEmail, wrapPlainText } from './emailHtmlFrame';

describe('wrapHtmlEmail', () => {
  it('embeds the sanitized body inside a full document with the reset styles', () => {
    const doc = wrapHtmlEmail('<p>hola</p>');
    expect(doc).toContain('<p>hola</p>');
    expect(doc).toContain('<base target="_blank">');
    expect(doc).toContain('background:#fff');
  });

  it('pins the document to the light color scheme so sender dark-mode media queries never fire', () => {
    // The OS/browser may prefer dark; the embedded email must stay light
    // (Gmail parity). Regression guard for the dark-background rendering bug.
    expect(wrapHtmlEmail('<p>x</p>')).toContain('color-scheme:light');
  });

  it('does not constrain email images with a global max-width', () => {
    // A percentage max-width inside an auto-layout table cell gives the image
    // a 0 min-content width and the cell can collapse to 0px (Amazon.es's
    // header logo vanished this way). Gmail applies no such reset either.
    expect(wrapHtmlEmail('<p>x</p>')).not.toContain('max-width');
  });
});

describe('wrapPlainText', () => {
  it('escapes HTML so a plain-text body cannot smuggle markup', () => {
    const doc = wrapPlainText('<script>alert(1)</script>');
    expect(doc).not.toContain('<script>alert(1)</script>');
    expect(doc).toContain('&lt;script&gt;');
  });
});

describe('escapeHtml', () => {
  it('escapes the three HTML-significant characters', () => {
    expect(escapeHtml('a & <b> c')).toBe('a &amp; &lt;b&gt; c');
  });
});

describe('hasRenderableBody', () => {
  it('rejects null, empty and whitespace-only bodies', () => {
    expect(hasRenderableBody(null)).toBe(false);
    expect(hasRenderableBody(undefined)).toBe(false);
    expect(hasRenderableBody('')).toBe(false);
    expect(hasRenderableBody(' \r\n ')).toBe(false);
  });

  it('accepts a body with visible content', () => {
    expect(hasRenderableBody('<p>x</p>')).toBe(true);
  });
});
