import { useEffect, useRef, useState } from 'react';
import { HelpCircle } from 'lucide-react';

// Static cheat-sheet of the search operators the backend understands. The
// operators are English (the closed decision: "operators in English only");
// the explanatory text is Spanish, matching the rest of the UI.
const OPERATORS: { syntax: string; description: string }[] = [
  { syntax: 'from:linkedin', description: 'el remitente contiene…' },
  { syntax: 'to:ana', description: 'el destinatario ("Para") contiene…' },
  { syntax: 'subject:factura', description: 'el asunto contiene…' },
  { syntax: 'has:attachment', description: 'con adjunto' },
  { syntax: 'before:2026/01/01', description: 'antes de una fecha (AAAA/MM/DD o AAAA-MM-DD)' },
  { syntax: 'after:2025/12/01', description: 'desde una fecha (incluida)' },
  { syntax: 'is:unread / is:read', description: 'no leídos / leídos' },
  { syntax: 'is:favorite', description: 'favoritos (alias is:starred)' },
  { syntax: 'in:inbox', description: 'restringe a una bandeja: inbox, sent, spam, trash' },
];

export default function SearchHelpPopover() {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  // Close on Escape or on a click outside the popover. Both listeners are
  // only mounted while open and torn down on cleanup, so the component does
  // not keep document-level listeners alive when collapsed. ConfirmPopover
  // delegates this to its parent; this popover owns its own open state, so
  // it implements the dismissal itself.
  useEffect(() => {
    if (!open) return;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false);
    };
    const handlePointerDown = (event: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener('keydown', handleKeyDown);
    document.addEventListener('mousedown', handlePointerDown);
    return () => {
      document.removeEventListener('keydown', handleKeyDown);
      document.removeEventListener('mousedown', handlePointerDown);
    };
  }, [open]);

  return (
    <div ref={containerRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((prev) => !prev)}
        aria-label="Ayuda de búsqueda"
        aria-expanded={open}
        className="flex h-10 w-10 items-center justify-center rounded-lg text-zinc-400 hover:bg-zinc-100 hover:text-zinc-600"
      >
        <HelpCircle className="h-5 w-5" />
      </button>
      {open && (
        <div className="absolute right-0 top-full z-30 mt-1 w-80 rounded-xl border border-zinc-200 bg-white p-4 shadow-lg">
          <p className="text-sm font-semibold text-zinc-900">Operadores de búsqueda</p>
          <ul className="mt-3 flex flex-col gap-2">
            {OPERATORS.map((op) => (
              <li key={op.syntax} className="flex flex-col gap-0.5">
                <code className="text-xs font-medium text-blue-700">{op.syntax}</code>
                <span className="text-xs leading-relaxed text-zinc-500">{op.description}</span>
              </li>
            ))}
          </ul>
          <p className="mt-3 border-t border-zinc-100 pt-3 text-xs leading-relaxed text-zinc-500">
            Se combinan con espacios (Y); usa comillas para frases:{' '}
            <code className="text-blue-700">subject:&quot;acción requerida&quot;</code>.
          </p>
        </div>
      )}
    </div>
  );
}
