import { Star } from 'lucide-react';

import ConversationMessageBody from './ConversationMessageBody';
import { formatDate } from '../../../lib/formatters';
import { useTranslation } from '../../../lib/i18n';
import type { EmailMetadataOut } from '../../../api/types/dto';
import type { EmailBox } from '../../../lib/types';

type Props = {
  message: EmailMetadataOut;
  expanded: boolean;
  onToggle: () => void;
};

// Maps the message's box to a short folder-label i18n key. ALL_MAIL (the
// normal inbox location) gets no label — only the "elsewhere" boxes are worth
// flagging in a chain that crosses folders.
const BOX_LABEL_KEYS: Partial<Record<EmailBox, string>> = {
  SENT: 'conversation.boxSent',
  SPAM: 'conversation.boxSpam',
  TRASH: 'conversation.boxTrash',
  ARCHIVE: 'conversation.boxArchive',
};

// Presentational card for one message of the conversation chain. The header
// (De + date + per-message indicators) is always visible and toggles
// expansion on click; the body is delegated to the ``ConversationMessageBody``
// container and mounted only while expanded (lazy load).
//
// No attachment clip is shown in the collapsed header on purpose: inside a
// ``ConversationOut`` every message's ``has_attachments`` is always false
// (backend B.lazy), so reading it would render a dead indicator. The real
// per-message attachments surface when the card is expanded and its body
// fetches ``EmailContentOut.attachments``.
export default function ConversationMessageCard({ message, expanded, onToggle }: Props) {
  const { t } = useTranslation();
  const fromLabel = message.from_name
    ? `${message.from_name} <${message.from_email}>`
    : message.from_email;
  const unread = !message.is_read;
  const folderLabelKey = BOX_LABEL_KEYS[message.box as EmailBox];

  return (
    <div className="overflow-hidden rounded-lg border border-zinc-200 bg-white">
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={expanded}
        className="flex w-full items-center gap-2 px-4 py-3 text-left transition-colors hover:bg-zinc-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-blue-600"
      >
        {unread && (
          <span
            className="h-2 w-2 shrink-0 rounded-full bg-blue-600"
            aria-label={t('conversation.unread')}
          />
        )}
        <span
          className={`min-w-0 flex-1 truncate text-[13px] ${
            unread ? 'font-semibold text-zinc-900' : 'font-normal text-zinc-700'
          }`}
        >
          {fromLabel}
        </span>
        {message.is_favorite && (
          <Star
            className="shrink-0 fill-amber-400 text-amber-400"
            style={{ width: 14, height: 14 }}
            strokeWidth={1.75}
            aria-label={t('conversation.favorite')}
          />
        )}
        {folderLabelKey && (
          <span className="shrink-0 rounded bg-zinc-100 px-1.5 py-0.5 text-[11px] font-medium text-zinc-500">
            {t(folderLabelKey)}
          </span>
        )}
        <span className="shrink-0 text-[12px] text-zinc-500">
          {formatDate(message.received_at)}
        </span>
      </button>
      {expanded && (
        <div className="border-t border-zinc-100 pt-3 pb-1">
          <div className="px-4 pb-2 text-[12px] text-zinc-500">
            {message.to_email && (
              <div className="flex flex-wrap items-center gap-x-1.5">
                <span className="font-medium text-zinc-700">{t('viewer.to')}</span>
                <span className="truncate">
                  {message.to_name && message.to_name !== message.to_email
                    ? `${message.to_name} <${message.to_email}>`
                    : message.to_email}
                </span>
              </div>
            )}
          </div>
          <ConversationMessageBody message={message} />
        </div>
      )}
    </div>
  );
}
