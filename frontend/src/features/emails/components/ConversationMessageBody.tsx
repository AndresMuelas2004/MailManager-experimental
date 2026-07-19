import { useCallback } from 'react';
import { MailMinus, Star, Trash2, Ban, Archive, Inbox } from 'lucide-react';

import Spinner from '../../../components/common/Spinner';
import AttachmentsList from './AttachmentsList';
import { hasRenderableBody, wrapHtmlEmail, wrapPlainText } from './emailHtmlFrame';
import { useTranslation } from '../../../lib/i18n';
import useEmailContent from '../hooks/useEmailContent';
import useAttachmentDownloader from '../hooks/useAttachmentDownloader';
import useFavorite from '../hooks/useFavorite';
import useEmailBulkActions from '../hooks/useEmailBulkActions';
import type { EmailMetadataOut } from '../../../api/types/dto';

type Props = {
  message: EmailMetadataOut;
};

const NOOP = async () => {};
const NOOP_SYNC = () => {};

// Container for a single expanded message of a conversation. Mirrors
// ``ViewerWithDownloader`` but per message: it owns the data-fetching hooks
// (content + downloader) and the per-message mutations (favourite, trash,
// spam, mark unread). Mounted ONLY when its card is expanded — that
// conditional mount IS the lazy body load (the content hook fetches on mount).
//
// Every call uses ``message.mailbox_id`` (the message's REAL mailbox, carried
// in the conversation payload), never the route's mailbox: a conversation
// inside a cross-mailbox virtual bandeja can hold messages living in different
// real mailboxes, and the backend validates ``account ∈ mailbox`` on every
// per-message path.
export default function ConversationMessageBody({ message }: Props) {
  const { t } = useTranslation();
  const { content, loading, error } = useEmailContent(message.mailbox_id, {
    account_id: message.account_id,
    provider_message_id: message.provider_message_id,
  });
  const downloader = useAttachmentDownloader({
    mailboxId: message.mailbox_id,
    accountId: message.account_id,
    providerMessageId: message.provider_message_id,
  });

  const favorites = useFavorite();
  // The bulk-actions hook requires a ``refresh`` + ``clearSelection``; this
  // surface has neither a per-page refresh nor a selection, so both are
  // no-ops. The hook still invalidates the listing prefixes in its own
  // ``onSuccess``, which is what refreshes the conversation row indicators.
  const bulk = useEmailBulkActions({ refresh: NOOP, clearSelection: NOOP_SYNC });

  const handleToggleFavorite = useCallback(() => {
    favorites
      .toggle({
        mailboxId: message.mailbox_id,
        accountId: message.account_id,
        providerMessageId: message.provider_message_id,
        favorite: !message.is_favorite,
      })
      .catch(() => {});
  }, [favorites, message]);

  const handleTrash = useCallback(() => {
    void bulk.moveToTrashItems([message]);
  }, [bulk, message]);

  const handleSpam = useCallback(() => {
    void bulk.spamItems([message]);
  }, [bulk, message]);

  const handleMarkUnread = useCallback(() => {
    void bulk.setReadStatusItems([message], false);
  }, [bulk, message]);

  const handleArchive = useCallback(() => {
    void bulk.archiveItems([message]);
  }, [bulk, message]);

  const handleUnarchive = useCallback(() => {
    void bulk.unarchiveItems([message]);
  }, [bulk, message]);

  let bodyFrame: React.ReactNode;
  if (loading) {
    bodyFrame = (
      <div className="flex h-40 items-center justify-center">
        <Spinner size="sm" />
      </div>
    );
  } else if (error) {
    bodyFrame = <div className="px-2 py-6 text-center text-sm text-red-600">{error.message}</div>;
  } else if (content && hasRenderableBody(content.html_body)) {
    bodyFrame = (
      <iframe
        title={t('viewer.iframeTitle')}
        srcDoc={wrapHtmlEmail(content.html_body)}
        sandbox="allow-popups allow-popups-to-escape-sandbox"
        referrerPolicy="strict-origin-when-cross-origin"
        className="h-[70vh] w-full border-0"
        // Pin the embedded document to light so sender dark-mode media
        // queries never fire (see emailHtmlFrame.ts).
        style={{ colorScheme: 'light' }}
      />
    );
  } else if (content && hasRenderableBody(content.text_body)) {
    bodyFrame = (
      <iframe
        title={t('viewer.iframeTitle')}
        srcDoc={wrapPlainText(content.text_body)}
        sandbox="allow-popups allow-popups-to-escape-sandbox"
        referrerPolicy="strict-origin-when-cross-origin"
        className="h-[70vh] w-full border-0"
        style={{ colorScheme: 'light' }}
      />
    );
  } else {
    bodyFrame = (
      <div className="px-2 py-6 text-center text-sm text-zinc-400">{t('viewer.noContent')}</div>
    );
  }

  return (
    <div className="flex flex-col">
      <div className="flex flex-wrap items-center gap-2 px-4 pb-2">
        <button
          type="button"
          onClick={handleToggleFavorite}
          disabled={favorites.toggling}
          aria-label={
            message.is_favorite ? t('conversation.removeFavorite') : t('conversation.markFavorite')
          }
          aria-pressed={message.is_favorite}
          className="inline-flex items-center gap-1.5 rounded-md border border-zinc-200 bg-white px-2.5 py-1 text-[12px] font-medium text-zinc-600 transition-colors hover:bg-zinc-50 disabled:cursor-not-allowed disabled:opacity-60"
        >
          <Star
            className={message.is_favorite ? 'fill-amber-400 text-amber-400' : 'text-zinc-400'}
            style={{ width: 14, height: 14 }}
            strokeWidth={1.75}
          />
          {t('conversation.favorite')}
        </button>
        <button
          type="button"
          onClick={handleMarkUnread}
          disabled={bulk.loading}
          aria-label={t('conversation.markUnread')}
          className="inline-flex items-center gap-1.5 rounded-md border border-zinc-200 bg-white px-2.5 py-1 text-[12px] font-medium text-zinc-600 transition-colors hover:bg-zinc-50 disabled:cursor-not-allowed disabled:opacity-60"
        >
          <MailMinus style={{ width: 14, height: 14 }} strokeWidth={1.75} />
          {t('conversation.notRead')}
        </button>
        {message.box === 'ALL_MAIL' && (
          <button
            type="button"
            onClick={handleArchive}
            disabled={bulk.loading}
            aria-label={t('conversation.archive')}
            className="inline-flex items-center gap-1.5 rounded-md border border-zinc-200 bg-white px-2.5 py-1 text-[12px] font-medium text-zinc-600 transition-colors hover:bg-zinc-50 disabled:cursor-not-allowed disabled:opacity-60"
          >
            <Archive style={{ width: 14, height: 14 }} strokeWidth={1.75} />
            {t('conversation.archive')}
          </button>
        )}
        {message.box === 'ARCHIVE' && (
          <button
            type="button"
            onClick={handleUnarchive}
            disabled={bulk.loading}
            aria-label={t('conversation.unarchive')}
            className="inline-flex items-center gap-1.5 rounded-md border border-zinc-200 bg-white px-2.5 py-1 text-[12px] font-medium text-zinc-600 transition-colors hover:bg-zinc-50 disabled:cursor-not-allowed disabled:opacity-60"
          >
            <Inbox style={{ width: 14, height: 14 }} strokeWidth={1.75} />
            {t('conversation.unarchive')}
          </button>
        )}
        <button
          type="button"
          onClick={handleSpam}
          disabled={bulk.loading}
          aria-label={t('conversation.markSpam')}
          className="inline-flex items-center gap-1.5 rounded-md border border-zinc-200 bg-white px-2.5 py-1 text-[12px] font-medium text-zinc-600 transition-colors hover:bg-zinc-50 disabled:cursor-not-allowed disabled:opacity-60"
        >
          <Ban style={{ width: 14, height: 14 }} strokeWidth={1.75} />
          {t('conversation.spam')}
        </button>
        <button
          type="button"
          onClick={handleTrash}
          disabled={bulk.loading}
          aria-label={t('conversation.moveToTrash')}
          className="inline-flex items-center gap-1.5 rounded-md border border-zinc-200 bg-white px-2.5 py-1 text-[12px] font-medium text-zinc-600 transition-colors hover:bg-zinc-50 disabled:cursor-not-allowed disabled:opacity-60"
        >
          <Trash2 style={{ width: 14, height: 14 }} strokeWidth={1.75} />
          {t('conversation.trash')}
        </button>
      </div>
      {bodyFrame}
      <AttachmentsList attachments={content?.attachments ?? []} downloader={downloader} />
    </div>
  );
}
