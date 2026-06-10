// Pure helpers for the rich-text composer body (HTML).
//
// These live in ``lib/`` — not inside ``components/ui/RichTextEditor`` —
// because both the component AND ``features/drafts/hooks/useComposerForm``
// need them, and a feature hook may not import from ``components/ui/``
// (see ``features/CLAUDE.md`` §8). They are domain-agnostic string
// functions, so ``lib/`` is the correct home.

// A TipTap/ProseMirror editor that the user emptied serialises to
// ``'<p></p>'`` (or a stray ``<br>`` / ``&nbsp;``), never to ``''``.
// ``normalizeEmpty`` collapses those "visually empty" documents to a
// real empty string so that emptiness checks, dirtiness comparison and
// the silent-draft bootstrap (which asserts ``body: ''``) all agree.
// Non-empty HTML is returned verbatim — we never rewrite real content.
export function normalizeEmpty(html: string): string {
  const stripped = html
    .replace(/<p>\s*<\/p>/g, '')
    .replace(/<br\s*\/?>/g, '')
    .replace(/&nbsp;/g, '')
    .trim();
  return stripped.length === 0 ? '' : html;
}

// True when the HTML has no visible text once tags and space entities
// are removed. Used by ``hasAnyContent`` so that a ``<p></p>`` or a lone
// ``<br>`` does not count as "the user wrote something".
export function htmlIsEmpty(html: string): boolean {
  const text = html
    .replace(/<[^>]*>/g, '')
    .replace(/&nbsp;/g, ' ')
    .trim();
  return text.length === 0;
}
