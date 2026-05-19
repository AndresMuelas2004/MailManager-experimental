import { Download, File, FileImage, FileSpreadsheet, FileText, FileWarning } from 'lucide-react';

import { formatBytes } from '../../../lib/attachments';
import Spinner from '../../../components/common/Spinner';
import type { AttachmentMetadata } from '../../../api/types/dto';
import type { DownloadStatus } from '../hooks/useDownloadQueue';

type Props = {
  attachment: AttachmentMetadata;
  status: DownloadStatus;
  onDownload: () => void;
  onCancel?: () => void;
};

function pickIcon(mime: string) {
  const lower = (mime || '').toLowerCase();
  if (lower.startsWith('image/')) return FileImage;
  if (lower.includes('pdf')) return FileText;
  if (
    lower.includes('spreadsheet') ||
    lower.includes('excel') ||
    lower.includes('csv') ||
    lower === 'text/csv'
  ) {
    return FileSpreadsheet;
  }
  if (lower.startsWith('text/') || lower.includes('word') || lower.includes('document')) {
    return FileText;
  }
  return File;
}

export default function AttachmentCard({ attachment, status, onDownload, onCancel }: Props) {
  const Icon = attachment.is_unavailable ? FileWarning : pickIcon(attachment.mime_type);
  const disabled = attachment.is_unavailable;
  const handleClick = () => {
    if (disabled) return;
    if (status === 'downloading' && onCancel) {
      onCancel();
      return;
    }
    onDownload();
  };

  return (
    <button
      type="button"
      onClick={handleClick}
      disabled={disabled}
      title={attachment.filename}
      className={[
        'flex w-full items-center gap-3 rounded-lg border px-3 py-2 text-left transition',
        disabled
          ? 'cursor-not-allowed border-zinc-200 bg-zinc-50 text-zinc-400 opacity-70'
          : 'border-zinc-200 bg-white hover:border-blue-300 hover:bg-blue-50',
      ].join(' ')}
    >
      <Icon className="h-5 w-5 shrink-0 text-zinc-500" />
      <div className="min-w-0 flex-1">
        <div className="truncate text-[13px] font-medium text-zinc-900">{attachment.filename}</div>
        <div className="text-[11px] text-zinc-500">
          {formatBytes(attachment.size)}
          {attachment.is_unavailable ? ' · No disponible en el servidor' : null}
          {status === 'queued' ? ' · En cola' : null}
          {status === 'error' ? ' · Error: no se pudo descargar' : null}
        </div>
      </div>
      <div className="ml-2 h-5 w-5 shrink-0 text-zinc-400">
        {status === 'downloading' ? (
          <Spinner />
        ) : (
          <Download className="h-5 w-5" />
        )}
      </div>
    </button>
  );
}
