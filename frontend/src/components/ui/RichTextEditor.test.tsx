/**
 * Light integration tests for ``RichTextEditor`` (TipTap/ProseMirror in
 * jsdom). The editor is mounted for real — no mocking of the component
 * (test/CLAUDE.md §4). jsdom's missing geometry APIs are polyfilled in
 * ``src/test/setup.ts``.
 *
 * ``css: false`` in vitest.config means the ``prose`` classes apply no
 * styling, so every assertion is on the serialised HTML / DOM, never on
 * appearance.
 *
 * The component is controlled (``value`` / ``onChange``). We mount it via a
 * tiny harness that mirrors the latest ``onChange`` HTML back into ``value``
 * (so the editor behaves as it does in production) and exposes the most
 * recent HTML for assertions.
 */

import { useState } from 'react';
import { act, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import RichTextEditor from './RichTextEditor';

function Harness({ onHtml, initial = '' }: { onHtml: (html: string) => void; initial?: string }) {
  const [value, setValue] = useState(initial);
  return (
    <RichTextEditor
      value={value}
      onChange={(html) => {
        setValue(html);
        onHtml(html);
      }}
    />
  );
}

/** Render the harness and wait a tick so the deferred ``useEditor`` mounts. */
async function mountEditor(initial = '') {
  const htmlSpy = vi.fn<(html: string) => void>();
  let latestHtml = initial;
  htmlSpy.mockImplementation((html) => {
    latestHtml = html;
  });
  render(<Harness onHtml={htmlSpy} initial={initial} />);
  // ``immediatelyRender: false`` defers the first render one tick.
  await act(async () => {
    await Promise.resolve();
  });
  // The contenteditable surface ProseMirror renders.
  const editable = await screen.findByLabelText('Cuerpo del mensaje');
  return { htmlSpy, editable, getLatestHtml: () => latestHtml };
}

afterEach(() => {
  vi.clearAllMocks();
});

describe('RichTextEditor — toolbar formatting', () => {
  it('applies bold to typed text', async () => {
    const user = userEvent.setup();
    const { editable, getLatestHtml } = await mountEditor();

    await user.click(editable);
    await user.keyboard('hello');
    // Select all, then toggle bold over the selection.
    await user.keyboard('{Control>}a{/Control}');
    await user.click(screen.getByRole('button', { name: 'Negrita' }));

    expect(getLatestHtml()).toContain('<strong>');
  });

  it('applies italic to typed text', async () => {
    const user = userEvent.setup();
    const { editable, getLatestHtml } = await mountEditor();

    await user.click(editable);
    await user.keyboard('hello');
    await user.keyboard('{Control>}a{/Control}');
    await user.click(screen.getByRole('button', { name: 'Cursiva' }));

    expect(getLatestHtml()).toContain('<em>');
  });

  it('applies underline to typed text', async () => {
    const user = userEvent.setup();
    const { editable, getLatestHtml } = await mountEditor();

    await user.click(editable);
    await user.keyboard('hello');
    await user.keyboard('{Control>}a{/Control}');
    await user.click(screen.getByRole('button', { name: 'Subrayado' }));

    expect(getLatestHtml()).toContain('<u>');
  });

  it('toggles a bullet list', async () => {
    const user = userEvent.setup();
    const { editable, getLatestHtml } = await mountEditor();

    await user.click(editable);
    await user.keyboard('item');
    await user.click(screen.getByRole('button', { name: 'Lista con viñetas' }));

    const html = getLatestHtml();
    expect(html).toContain('<ul>');
    expect(html).toContain('<li>');
  });

  it('toggles an ordered list', async () => {
    const user = userEvent.setup();
    const { editable, getLatestHtml } = await mountEditor();

    await user.click(editable);
    await user.keyboard('item');
    await user.click(screen.getByRole('button', { name: 'Lista numerada' }));

    const html = getLatestHtml();
    expect(html).toContain('<ol>');
    expect(html).toContain('<li>');
  });

  it('wraps the selection in a hardened link via the popover', async () => {
    const user = userEvent.setup();
    const { editable, getLatestHtml } = await mountEditor();

    await user.click(editable);
    await user.keyboard('site');
    await user.keyboard('{Control>}a{/Control}');
    // Open the link popover and submit an allow-listed URL.
    await user.click(screen.getByRole('button', { name: 'Insertar enlace' }));
    const urlInput = screen.getByPlaceholderText('https://ejemplo.com');
    await user.type(urlInput, 'https://example.com');
    await user.click(screen.getByRole('button', { name: 'Aplicar' }));

    const html = getLatestHtml();
    expect(html).toContain('<a ');
    expect(html).toContain('href="https://example.com"');
    // The Link extension hardens every link to open safely in a new tab.
    expect(html).toContain('target="_blank"');
    expect(html).toContain('rel="noopener noreferrer nofollow"');
  });
});

describe('RichTextEditor — remove formatting', () => {
  it('clears marks and nodes from a bold list', async () => {
    const user = userEvent.setup();
    // Seed an already-formatted document, then strip it.
    const { editable, getLatestHtml } = await mountEditor(
      '<ul><li><strong>bold item</strong></li></ul>',
    );

    await user.click(editable);
    await user.keyboard('{Control>}a{/Control}');
    await user.click(screen.getByRole('button', { name: 'Quitar formato' }));

    const html = getLatestHtml();
    expect(html).not.toContain('<strong>');
    expect(html).not.toContain('<ul>');
    // The text content survives the clear.
    expect(html).toContain('bold item');
  });
});

describe('RichTextEditor — empty normalisation', () => {
  it('emits an empty string when the user clears all content', async () => {
    const user = userEvent.setup();
    const { editable, htmlSpy } = await mountEditor();

    await user.click(editable);
    await user.keyboard('x');
    await user.keyboard('{Control>}a{/Control}');
    await user.keyboard('{Backspace}');

    // ``onUpdate`` runs ``normalizeEmpty(editor.getHTML())``, so an emptied
    // editor reports '' rather than the residual '<p></p>'.
    const calls = htmlSpy.mock.calls.map((c) => c[0]);
    expect(calls).toContain('');
  });
});

describe('RichTextEditor — restricted schema', () => {
  // The editor never dumps raw HTML: ProseMirror PARSES incoming content
  // against the restricted schema. Seeding unsupported tags (the same effect
  // a paste of newsletter HTML has) drops everything outside the allowed set,
  // keeping the text. This is the safe-rendering guarantee (components/CLAUDE.md
  // §3.7) — verified at the schema level rather than via a brittle jsdom paste.
  it('drops headings, tables, inline styles and scripts, keeping the text', async () => {
    await mountEditor(
      '<h1>Title</h1>' +
        '<table><tr><td>cell</td></tr></table>' +
        '<p style="color:red">styled</p>' +
        '<script>steal()</script>' +
        '<p>keep</p>',
    );
    const editable = await screen.findByLabelText('Cuerpo del mensaje');
    const html = editable.innerHTML;
    // Unsupported structural tags are gone (collapsed to paragraphs / dropped).
    expect(html).not.toContain('<h1');
    expect(html).not.toContain('<table');
    expect(html).not.toContain('<td');
    expect(html).not.toContain('<script');
    // Inline ``style`` is not part of the schema, so it never survives.
    expect(html).not.toContain('style=');
    // The textual content is preserved.
    expect(html).toContain('keep');
    expect(html).toContain('cell');
  });

  it('preserves the allowed mark set on seeded content', async () => {
    await mountEditor('<p><strong>b</strong> <em>i</em> <u>u</u></p><ul><li>one</li></ul>');
    const editable = await screen.findByLabelText('Cuerpo del mensaje');
    const html = editable.innerHTML;
    expect(html).toContain('<strong>');
    expect(html).toContain('<em>');
    expect(html).toContain('<u>');
    expect(html).toContain('<ul>');
    expect(html).toContain('<li>');
  });
});
