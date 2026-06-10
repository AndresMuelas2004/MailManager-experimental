import { useEffect, useState } from 'react';
import { useEditor, useEditorState, EditorContent, type Editor } from '@tiptap/react';
import StarterKit from '@tiptap/starter-kit';
import { Placeholder } from '@tiptap/extensions';
import {
  Bold,
  Italic,
  Underline,
  List,
  ListOrdered,
  Link as LinkIcon,
  RemoveFormatting,
} from 'lucide-react';

import { normalizeEmpty } from '../../lib/richText';

export type RichTextEditorProps = {
  value: string; // HTML of the message body
  onChange: (html: string) => void;
  placeholder?: string;
  disabled?: boolean;
  ariaLabel?: string;
};

// Same allow-list the StarterKit Link extension is configured with
// below. Validating the popover-entered URL against it before calling
// ``setLink`` satisfies components/CLAUDE.md §3.7 (never feed an ``href``
// that could resolve to ``javascript:`` / ``data:text/html`` into a link).
// The backend re-sanitises on save/send; this is the client-side guard.
const ALLOWED_LINK_PROTOCOLS = ['http', 'https', 'mailto'] as const;

function isAllowedLinkHref(raw: string): boolean {
  const trimmed = raw.trim();
  if (trimmed.length === 0) return false;
  try {
    // ``mailto:foo@bar`` and ``https://…`` both parse with the URL ctor.
    const protocol = new URL(trimmed).protocol.replace(/:$/, '').toLowerCase();
    return (ALLOWED_LINK_PROTOCOLS as readonly string[]).includes(protocol);
  } catch {
    // Relative / scheme-less inputs are rejected: we require an explicit,
    // allow-listed scheme so a bare ``javascript:alert(1)`` typed without
    // ``//`` cannot slip through a lenient parse.
    return false;
  }
}

type ToolbarState = {
  bold: boolean;
  italic: boolean;
  underline: boolean;
  bulletList: boolean;
  orderedList: boolean;
  link: boolean;
};

const EMPTY_TOOLBAR_STATE: ToolbarState = {
  bold: false,
  italic: false,
  underline: false,
  bulletList: false,
  orderedList: false,
  link: false,
};

function ToolbarButton({
  onClick,
  active,
  disabled,
  label,
  children,
}: {
  onClick: () => void;
  active?: boolean;
  disabled?: boolean;
  label: string;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onMouseDown={(e) => e.preventDefault()} // keep the editor selection on click
      onClick={onClick}
      disabled={disabled}
      aria-label={label}
      aria-pressed={active}
      title={label}
      className={[
        'inline-flex h-8 w-8 items-center justify-center rounded-md transition-colors disabled:opacity-50',
        active
          ? 'bg-zinc-200 text-zinc-900'
          : 'text-zinc-600 hover:bg-zinc-100 hover:text-zinc-900',
      ].join(' ')}
    >
      {children}
    </button>
  );
}

function LinkPopover({ editor, disabled }: { editor: Editor; disabled?: boolean }) {
  const [open, setOpen] = useState(false);
  const [href, setHref] = useState('');
  const [invalid, setInvalid] = useState(false);

  const active = useEditorState({
    editor,
    selector: ({ editor: e }) => e.isActive('link'),
  });

  const openPopover = () => {
    // Prefill with the existing link href when the cursor is on a link.
    const existing = (editor.getAttributes('link').href as string | undefined) ?? '';
    setHref(existing);
    setInvalid(false);
    setOpen(true);
  };

  const apply = () => {
    if (!isAllowedLinkHref(href)) {
      setInvalid(true);
      return;
    }
    editor.chain().focus().extendMarkRange('link').setLink({ href: href.trim() }).run();
    setOpen(false);
  };

  const remove = () => {
    editor.chain().focus().extendMarkRange('link').unsetLink().run();
    setOpen(false);
  };

  return (
    <div className="relative">
      <ToolbarButton
        onClick={openPopover}
        active={Boolean(active)}
        disabled={disabled}
        label="Insertar enlace"
      >
        <LinkIcon className="h-4 w-4" />
      </ToolbarButton>
      {open && (
        <div className="absolute top-full left-0 z-20 mt-1 flex w-64 flex-col gap-2 rounded-[10px] border border-zinc-200 bg-white p-2 shadow-lg">
          <input
            type="url"
            value={href}
            autoFocus
            onChange={(e) => {
              setHref(e.target.value);
              if (invalid) setInvalid(false);
            }}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault();
                apply();
              } else if (e.key === 'Escape') {
                e.preventDefault();
                setOpen(false);
              }
            }}
            placeholder="https://ejemplo.com"
            className="h-9 rounded-md border-[1.5px] border-zinc-200 px-2 text-sm text-zinc-900 placeholder:text-zinc-400 focus:border-blue-600 focus:outline-none"
          />
          {invalid && (
            <p className="text-xs text-red-600">Introduce una URL http(s) o mailto válida.</p>
          )}
          <div className="flex items-center justify-end gap-2">
            {active && (
              <button
                type="button"
                onClick={remove}
                className="rounded-md px-2 py-1 text-xs font-medium text-zinc-600 hover:bg-zinc-100"
              >
                Quitar
              </button>
            )}
            <button
              type="button"
              onClick={apply}
              className="rounded-md bg-blue-600 px-3 py-1 text-xs font-semibold text-white hover:bg-blue-700"
            >
              Aplicar
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function Toolbar({ editor, disabled }: { editor: Editor; disabled?: boolean }) {
  const state =
    useEditorState({
      editor,
      selector: ({ editor: e }): ToolbarState => ({
        bold: e.isActive('bold'),
        italic: e.isActive('italic'),
        underline: e.isActive('underline'),
        bulletList: e.isActive('bulletList'),
        orderedList: e.isActive('orderedList'),
        link: e.isActive('link'),
      }),
    }) ?? EMPTY_TOOLBAR_STATE;

  return (
    <div className="flex items-center gap-0.5 border-b border-zinc-200 px-1.5 py-1">
      <ToolbarButton
        onClick={() => editor.chain().focus().toggleBold().run()}
        active={state.bold}
        disabled={disabled}
        label="Negrita"
      >
        <Bold className="h-4 w-4" />
      </ToolbarButton>
      <ToolbarButton
        onClick={() => editor.chain().focus().toggleItalic().run()}
        active={state.italic}
        disabled={disabled}
        label="Cursiva"
      >
        <Italic className="h-4 w-4" />
      </ToolbarButton>
      <ToolbarButton
        onClick={() => editor.chain().focus().toggleUnderline().run()}
        active={state.underline}
        disabled={disabled}
        label="Subrayado"
      >
        <Underline className="h-4 w-4" />
      </ToolbarButton>
      <ToolbarButton
        onClick={() => editor.chain().focus().toggleBulletList().run()}
        active={state.bulletList}
        disabled={disabled}
        label="Lista con viñetas"
      >
        <List className="h-4 w-4" />
      </ToolbarButton>
      <ToolbarButton
        onClick={() => editor.chain().focus().toggleOrderedList().run()}
        active={state.orderedList}
        disabled={disabled}
        label="Lista numerada"
      >
        <ListOrdered className="h-4 w-4" />
      </ToolbarButton>
      <LinkPopover editor={editor} disabled={disabled} />
      <ToolbarButton
        onClick={() => editor.chain().focus().unsetAllMarks().clearNodes().run()}
        disabled={disabled}
        label="Quitar formato"
      >
        <RemoveFormatting className="h-4 w-4" />
      </ToolbarButton>
    </div>
  );
}

// Controlled WYSIWYG editor that emits HTML, mirroring the
// ``value``/``onChange`` contract the plain ``<textarea>`` used to have.
//
// Safe-rendering note (components/CLAUDE.md §3.7): this editor does NOT use
// ``dangerouslySetInnerHTML``. ``EditorContent`` renders through ProseMirror
// over real DOM nodes; the initial/seeded HTML enters via ``content`` /
// ``setContent``, which ProseMirror PARSES against the restricted schema
// (it is not dumped as innerHTML). The Link mark is locked to an allow-list
// of protocols (``http``/``https``/``mailto``), and popover-entered URLs are
// validated against the same list before ``setLink``. The body is the user's
// own content sent to the backend (which re-sanitises authoritatively), not
// third-party HTML rendered back to the user.
export default function RichTextEditor({
  value,
  onChange,
  placeholder,
  disabled,
  ariaLabel,
}: RichTextEditorProps) {
  const editor = useEditor({
    extensions: [
      StarterKit.configure({
        heading: false,
        codeBlock: false,
        code: false,
        strike: false,
        horizontalRule: false,
        // Kept active: paragraph, text, document, bold, italic, underline,
        // bulletList, orderedList, listItem, blockquote (renders the
        // reply/forward quote seeded from the backend), hardBreak, undoRedo,
        // dropcursor, gapcursor, listKeymap, trailingNode.
        link: {
          openOnClick: false, // editable inside the composer (StarterKit default is true)
          autolink: true,
          linkOnPaste: true,
          protocols: [...ALLOWED_LINK_PROTOCOLS],
          HTMLAttributes: { rel: 'noopener noreferrer nofollow', target: '_blank' },
        },
      }),
      // Placeholder ships inside ``@tiptap/extensions`` (already pulled in by
      // StarterKit) — no extra top-level dependency. It adds the
      // ``is-editor-empty`` class + ``data-placeholder`` attribute that
      // ``globals.css`` renders as ghost text on the empty document.
      Placeholder.configure({ placeholder: placeholder ?? 'Escribe tu mensaje...' }),
    ],
    content: value,
    editable: !disabled,
    // Vite app with no SSR. ``immediatelyRender: false`` defers the first
    // render one tick, which avoids TipTap's hydration/double-render warning
    // under React 19 StrictMode (dev) and jsdom (tests). Behaviour-neutral in
    // the browser.
    immediatelyRender: false,
    onUpdate: ({ editor: e }) => onChange(normalizeEmpty(e.getHTML())),
    editorProps: {
      attributes: {
        class: 'prose prose-sm max-w-none focus:outline-none min-h-[8rem] px-3 py-2',
        'aria-label': ariaLabel ?? 'Cuerpo del mensaje',
      },
    },
  });

  // ``content``/``editable`` are read only at init by ``useEditor``. Keep the
  // editor's editability in sync when ``disabled`` flips mid-send: without
  // this the toolbar greys out but the contenteditable would still accept
  // input.
  useEffect(() => {
    editor?.setEditable(!disabled);
  }, [editor, disabled]);

  // Seed external ``value`` changes (open draft / reply / discard) without
  // clobbering the cursor while typing. Compare NORMALISED on both sides so
  // an equivalent ``<p></p>`` residual from the backend does not trigger a
  // re-seed every render, and only call ``setContent`` when the incoming
  // content really differs from what the editor already holds. ``emitUpdate:
  // false`` prevents the seed from looping back through ``onChange``.
  useEffect(() => {
    if (!editor) return;
    const incoming = normalizeEmpty(value);
    const current = normalizeEmpty(editor.getHTML());
    if (incoming !== current) {
      editor.commands.setContent(value || '', { emitUpdate: false });
    }
  }, [value, editor]);

  return (
    <div
      className={[
        'flex flex-col rounded-[10px] border-[1.5px] border-zinc-200 focus-within:border-blue-600',
        disabled ? 'pointer-events-none opacity-60' : '',
      ].join(' ')}
    >
      {editor && <Toolbar editor={editor} disabled={disabled} />}
      <EditorContent editor={editor} />
    </div>
  );
}
