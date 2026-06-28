import { useEffect, useRef } from 'react';

import Modal from '../../../components/common/Modal';
import Spinner from '../../../components/common/Spinner';
import type { UseAttachmentDownloaderReturn } from '../hooks/useAttachmentDownloader';
import type { UiError } from '../../../api/client/errors';
import AttachmentsList from './AttachmentsList';
import { hasRenderableBody, wrapHtmlEmail, wrapPlainText } from './emailHtmlFrame';
import { buildAccountMap, formatDate, resolveAccount } from '../../../lib/formatters';
import { useTranslation } from '../../../lib/i18n';
import type { EmailMetadataOut, AccountOut, EmailContentOut } from '../../../api/types/dto';

type Props = {
  email: EmailMetadataOut;
  accounts: AccountOut[];
  content: EmailContentOut | null;
  loading: boolean;
  error: UiError | null;
  onClose: () => void;
  onRead: (email: EmailMetadataOut) => Promise<void>;
  downloader: UseAttachmentDownloaderReturn;
  onReply: (email: EmailMetadataOut) => void | Promise<void>;
  onReplyAll: (email: EmailMetadataOut) => void | Promise<void>;
  onForward: (email: EmailMetadataOut) => void | Promise<void>;
};

export default function EmailViewer({
  email,
  accounts,
  content,
  loading,
  error,
  onClose,
  onRead,
  downloader,
  onReply,
  onReplyAll,
  onForward,
}: Props) {
  const { t } = useTranslation();
  const readTriggered = useRef(false);
  useEffect(() => {
    if (readTriggered.current) return;
    if (email.is_read) return;
    readTriggered.current = true;
    onRead(email).catch(() => {});
  }, [email, onRead]);

  const { providerName, accountEmail } = resolveAccount(
    email.account_id,
    buildAccountMap(accounts),
  );
  const fromLabel = email.from_name ? `${email.from_name} <${email.from_email}>` : email.from_email;
  const toLabel = email.to_email
    ? email.to_name && email.to_name !== email.to_email
      ? `${email.to_name} <${email.to_email}>`
      : email.to_email
    : null;

  const subject = email.subject ?? t('common.noSubject');

  let body: React.ReactNode;
  if (loading) {
    body = (
      <div className="flex h-[70vh] items-center justify-center">
        <Spinner />
      </div>
    );
  } else if (error) {
    body = (
      <div className="flex h-[70vh] items-center justify-center px-6 text-center text-sm text-red-600">
        {error.message}
      </div>
    );
  } else if (content && hasRenderableBody(content.html_body)) {
    body = (
      <iframe
        title={t('viewer.iframeTitle')}
        srcDoc={wrapHtmlEmail(content.html_body)}
        sandbox="allow-popups allow-popups-to-escape-sandbox"
        referrerPolicy="strict-origin-when-cross-origin"
        className="h-[70vh] w-full border-0"
      />
    );
  } else if (content && hasRenderableBody(content.text_body)) {
    body = (
      <iframe
        title={t('viewer.iframeTitle')}
        srcDoc={wrapPlainText(content.text_body)}
        sandbox="allow-popups allow-popups-to-escape-sandbox"
        referrerPolicy="strict-origin-when-cross-origin"
        className="h-[70vh] w-full border-0"
      />
    );
  } else {
    body = (
      <div className="flex h-[70vh] items-center justify-center text-sm text-zinc-400">
        {t('viewer.noContent')}
      </div>
    );
  }

  return (
    <Modal open mobileFullScreen onClose={onClose} ariaLabel={t('viewer.emailAria', { subject })}>
      <div className="flex flex-col gap-1.5 border-b border-zinc-200 px-6 pt-6 pb-4 pr-14">
        <h2 className="text-[20px] font-semibold leading-tight tracking-tight text-zinc-900">
          {subject}
        </h2>
        <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[13px] text-zinc-600">
          <span className="font-medium text-zinc-900">{t('viewer.from')}</span>
          <span className="truncate">{fromLabel}</span>
          <span className="text-zinc-300">·</span>
          <span>{formatDate(email.received_at)}</span>
        </div>
        {toLabel && (
          <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[13px] text-zinc-600">
            <span className="font-medium text-zinc-900">{t('viewer.to')}</span>
            <span className="truncate">{toLabel}</span>
          </div>
        )}
        {accountEmail && (
          <div className="flex items-center gap-2 text-[12px] text-zinc-500">
            <span className="font-medium">{t('viewer.account')}</span>
            <span>{providerName}</span>
            <span className="text-zinc-300">·</span>
            <span className="truncate">{accountEmail}</span>
          </div>
        )}
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={() => {
              void onReply(email);
            }}
            className="cursor-pointer rounded-[10px] border border-zinc-200 bg-white px-3 py-1.5 text-[13px] font-medium text-zinc-700 shadow-sm transition-all hover:border-zinc-300 hover:bg-zinc-100 hover:text-zinc-900 hover:shadow active:bg-zinc-200"
          >
            {t('viewer.reply')}
          </button>
          <button
            type="button"
            onClick={() => {
              void onReplyAll(email);
            }}
            className="cursor-pointer rounded-[10px] border border-zinc-200 bg-white px-3 py-1.5 text-[13px] font-medium text-zinc-700 shadow-sm transition-all hover:border-zinc-300 hover:bg-zinc-100 hover:text-zinc-900 hover:shadow active:bg-zinc-200"
          >
            {t('viewer.replyAll')}
          </button>
          <button
            type="button"
            onClick={() => {
              void onForward(email);
            }}
            className="cursor-pointer rounded-[10px] border border-zinc-200 bg-white px-3 py-1.5 text-[13px] font-medium text-zinc-700 shadow-sm transition-all hover:border-zinc-300 hover:bg-zinc-100 hover:text-zinc-900 hover:shadow active:bg-zinc-200"
          >
            {t('viewer.forward')}
          </button>
        </div>
      </div>
      <div className="flex flex-1 flex-col overflow-auto bg-white">
        {body}
        <AttachmentsList attachments={content?.attachments ?? []} downloader={downloader} />
      </div>
    </Modal>
  );
}
