import { Download, File, FileImage, FileSpreadsheet, FileText, FileWarning } from 'lucide-react';

import { formatBytes } from '../../../lib/attachments';
import { useTranslation } from '../../../lib/i18n';
import Spinner from '../../../components/common/Spinner';
import type { AttachmentMetadata } from '../../../api/types/dto';
import type { DownloadStatus } from '../hooks/useDownloadQueue';

type Props = {
  attachment: AttachmentMetadata;
  status: DownloadStatus;
  onDownload: () => void;
  onCancel?: () => void;
};

function FileTypeIcon({ mime, className }: { mime: string; className: string }) {
  const lower = (mime || '').toLowerCase();
  if (lower.startsWith('image/')) return <FileImage className={className} />;
  if (lower.includes('pdf')) return <FileText className={className} />;
  if (
    lower.includes('spreadsheet') ||
    lower.includes('excel') ||
    lower.includes('csv') ||
    lower === 'text/csv'
  ) {
    return <FileSpreadsheet className={className} />;
  }
  if (lower.startsWith('text/') || lower.includes('word') || lower.includes('document')) {
    return <FileText className={className} />;
  }
  return <File className={className} />;
}

export default function AttachmentCard({ attachment, status, onDownload, onCancel }: Props) {
  const { t } = useTranslation();
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
      {attachment.is_unavailable ? (
        <FileWarning className="h-5 w-5 shrink-0 text-zinc-500" />
      ) : (
        <FileTypeIcon mime={attachment.mime_type} className="h-5 w-5 shrink-0 text-zinc-500" />
      )}
      <div className="min-w-0 flex-1">
        <div className="truncate text-[13px] font-medium text-zinc-900">{attachment.filename}</div>
        <div className="text-[11px] text-zinc-500">
          {formatBytes(attachment.size)}
          {attachment.is_unavailable ? ` · ${t('attachments.unavailable')}` : null}
          {status === 'queued' ? ` · ${t('attachments.queued')}` : null}
          {status === 'error' ? ` · ${t('attachments.error')}` : null}
        </div>
      </div>
      <div className="ml-2 h-5 w-5 shrink-0 text-zinc-400">
        {status === 'downloading' ? <Spinner /> : <Download className="h-5 w-5" />}
      </div>
    </button>
  );
}
