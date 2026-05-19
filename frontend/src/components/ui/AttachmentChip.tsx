import { File, FileImage, FileSpreadsheet, FileText, X } from 'lucide-react';

import { formatBytes } from '../../lib/attachments';

// Minimal display contract — kept inline to respect the
// components/ui boundary (no imports from app/). The composer hook
// keeps a richer state object whose shape is a superset of this one.
export type ComposerAttachmentChipDisplay = {
  id: string;
  filename: string;
  mimeType: string;
  size: number;
  status: 'uploading' | 'uploaded' | 'failed';
  progress?: number;
};

type Props = {
  chip: ComposerAttachmentChipDisplay;
  onRemove: () => void;
};

function pickIcon(mime: string) {
  const lower = (mime || '').toLowerCase();
  if (lower.startsWith('image/')) return FileImage;
  if (lower.includes('pdf')) return FileText;
  if (lower.includes('spreadsheet') || lower.includes('excel') || lower.includes('csv')) {
    return FileSpreadsheet;
  }
  if (lower.startsWith('text/') || lower.includes('word') || lower.includes('document')) {
    return FileText;
  }
  return File;
}

function truncate(name: string, max = 20): string {
  if (name.length <= max) return name;
  const dot = name.lastIndexOf('.');
  if (dot < 0) return `${name.slice(0, max - 1)}…`;
  const ext = name.slice(dot);
  const head = name.slice(0, Math.max(1, max - 1 - ext.length));
  return `${head}…${ext}`;
}

export default function AttachmentChip({ chip, onRemove }: Props) {
  const Icon = pickIcon(chip.mimeType);
  const failed = chip.status === 'failed';
  const uploading = chip.status === 'uploading';
  const containerClass = [
    'inline-flex max-w-[260px] items-center gap-2 rounded-full border px-3 py-1.5 text-[12px]',
    failed
      ? 'border-red-300 bg-red-50 text-red-800'
      : uploading
        ? 'border-blue-300 bg-blue-50 text-zinc-800'
        : 'border-zinc-200 bg-white text-zinc-800',
  ].join(' ');

  return (
    <span className={containerClass} title={chip.filename}>
      <Icon className="h-3.5 w-3.5 shrink-0 text-zinc-500" />
      <span className="truncate font-medium">{truncate(chip.filename)}</span>
      <span className="text-[11px] text-zinc-500">{formatBytes(chip.size)}</span>
      {uploading ? (
        <span className="text-[11px] text-blue-600">{chip.progress ?? 0}%</span>
      ) : null}
      <button
        type="button"
        onClick={onRemove}
        aria-label={`Quitar ${chip.filename}`}
        className="ml-1 rounded-full p-0.5 text-zinc-500 hover:bg-zinc-100 hover:text-zinc-900"
      >
        <X className="h-3.5 w-3.5" />
      </button>
    </span>
  );
}
