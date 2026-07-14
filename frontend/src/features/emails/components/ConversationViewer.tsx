import { useMemo, useState } from 'react';

import Modal from '../../../components/common/Modal';
import Spinner from '../../../components/common/Spinner';
import ConversationMessageCard from './ConversationMessageCard';
import { normaliseSubject } from '../../../lib/formatters';
import { useTranslation } from '../../../lib/i18n';
import type { UiError } from '../../../api/client/errors';
import type { EmailMetadataOut } from '../../../api/types/dto';

type Props = {
  messages: EmailMetadataOut[];
  // Key of the message expanded by default — the email the user opened, not the
  // most-recent one. Stable across the chain arriving, so the opened email stays
  // expanded while the rest fill in collapsed.
  defaultExpandedKey: string;
  // The conversation chain is still loading in the background. Non-blocking: the
  // opened email's body is already visible; this only drives a discreet footer
  // indicator.
  threadLoading: boolean;
  error: UiError | null;
  onClose: () => void;
  onReply: (email: EmailMetadataOut) => void | Promise<void>;
  onReplyAll: (email: EmailMetadataOut) => void | Promise<void>;
  onForward: (email: EmailMetadataOut) => void | Promise<void>;
};

function messageKey(message: EmailMetadataOut): string {
  return `${message.account_id}|${message.provider_message_id}`;
}

// Presentational conversation viewer: a Modal with the thread header (base
// subject + Reply / Reply All / Forward that act on the most-recent message)
// and the chain of message cards in chronological ascending order.
//
// The opened email's body is NOT gated behind the chain fetch: ``messages``
// always includes the opened email (merged by the container), so the card list
// renders immediately and the opened email's body paints from the warmed cache
// while ``/conversation`` is still loading. A discreet footer indicator shows
// the chain is filling in; a chain-fetch error is a non-blocking notice (the
// opened email stays visible) instead of replacing the whole viewer.
//
// Expansion is local UI state: the OPENED message starts expanded (its
// ``defaultExpandedKey``), the rest collapsed; clicking a header toggles it.
// The data-fetching and mutations live in the container
// (``ConversationViewerMount``) and the per-card body container — this component
// never fetches.
export default function ConversationViewer({
  messages,
  defaultExpandedKey,
  threadLoading,
  error,
  onClose,
  onReply,
  onReplyAll,
  onForward,
}: Props) {
  const { t } = useTranslation();

  // Expansion is derived at render time, not seeded by an effect: the opened
  // message is expanded BY DEFAULT and every other one is collapsed.
  // ``toggledIds`` records only the keys the user explicitly flipped, so a
  // card is expanded when its default differs from whether the user toggled
  // it (XOR). ``defaultExpandedKey`` is stable across the chain arriving, so
  // the opened email stays expanded without a setState-in-effect.
  const [toggledIds, setToggledIds] = useState<Set<string>>(() => new Set());

  const isExpanded = (key: string): boolean => {
    const defaultExpanded = key === defaultExpandedKey;
    return toggledIds.has(key) ? !defaultExpanded : defaultExpanded;
  };

  const toggle = (key: string) => {
    setToggledIds((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  const headerSubject = useMemo(() => {
    if (messages.length === 0) return t('conversation.title');
    return normaliseSubject(messages[messages.length - 1].subject);
  }, [messages, t]);

  // Reply / Reply All / Forward act on the most-recent message of the chain.
  // Since ``messages`` always includes the opened email, before the chain loads
  // this is the opened email itself (natural fallback), and after it loads it is
  // the thread's newest message.
  const last = messages.length > 0 ? messages[messages.length - 1] : null;

  return (
    <Modal
      open
      mobileFullScreen
      onClose={onClose}
      ariaLabel={t('conversation.ariaLabel', { subject: headerSubject })}
    >
      <div className="flex flex-col gap-2 border-b border-zinc-200 px-6 pt-6 pb-4 pr-14">
        <h2 className="text-[20px] font-semibold leading-tight tracking-tight text-zinc-900">
          {headerSubject}
        </h2>
        {last && (
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={() => {
                void onReply(last);
              }}
              className="cursor-pointer rounded-[10px] border border-zinc-200 bg-white px-3 py-1.5 text-[13px] font-medium text-zinc-700 shadow-sm transition-all hover:border-zinc-300 hover:bg-zinc-100 hover:text-zinc-900 hover:shadow active:bg-zinc-200"
            >
              {t('viewer.reply')}
            </button>
            <button
              type="button"
              onClick={() => {
                void onReplyAll(last);
              }}
              className="cursor-pointer rounded-[10px] border border-zinc-200 bg-white px-3 py-1.5 text-[13px] font-medium text-zinc-700 shadow-sm transition-all hover:border-zinc-300 hover:bg-zinc-100 hover:text-zinc-900 hover:shadow active:bg-zinc-200"
            >
              {t('viewer.replyAll')}
            </button>
            <button
              type="button"
              onClick={() => {
                void onForward(last);
              }}
              className="cursor-pointer rounded-[10px] border border-zinc-200 bg-white px-3 py-1.5 text-[13px] font-medium text-zinc-700 shadow-sm transition-all hover:border-zinc-300 hover:bg-zinc-100 hover:text-zinc-900 hover:shadow active:bg-zinc-200"
            >
              {t('viewer.forward')}
            </button>
          </div>
        )}
      </div>
      <div className="flex flex-1 flex-col overflow-auto bg-[#F9FAFB]">
        {error && (
          <div className="px-6 pt-3 text-[13px] text-amber-700">
            {t('conversation.threadError')}
          </div>
        )}
        {messages.length === 0 ? (
          <div className="flex h-[40vh] items-center justify-center text-sm text-zinc-400">
            {t('conversation.empty')}
          </div>
        ) : (
          <div className="flex flex-col gap-2 px-6 py-4">
            {messages.map((message) => {
              const key = messageKey(message);
              return (
                <ConversationMessageCard
                  key={key}
                  message={message}
                  expanded={isExpanded(key)}
                  onToggle={() => toggle(key)}
                />
              );
            })}
          </div>
        )}
        {threadLoading && (
          <div className="flex items-center justify-center gap-2 px-6 pb-4 text-[12px] text-zinc-500">
            <Spinner size="sm" />
            {t('conversation.threadLoading')}
          </div>
        )}
      </div>
    </Modal>
  );
}
