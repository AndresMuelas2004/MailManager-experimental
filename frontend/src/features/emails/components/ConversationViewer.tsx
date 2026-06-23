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
  loading: boolean;
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
// and the chain of message cards in chronological ascending order. Expansion
// is local UI state: the last (most-recent) message starts expanded, the rest
// collapsed; clicking a header toggles it. The data-fetching and mutations
// live in the container (``ConversationViewerMount``) and the per-card body
// container — this component never fetches.
export default function ConversationViewer({
  messages,
  loading,
  error,
  onClose,
  onReply,
  onReplyAll,
  onForward,
}: Props) {
  const { t } = useTranslation();
  const lastKey = messages.length > 0 ? messageKey(messages[messages.length - 1]) : null;

  // Expansion is derived at render time, not seeded by an effect: the most-
  // recent message is expanded BY DEFAULT and every other one is collapsed.
  // ``toggledIds`` records only the keys the user explicitly flipped, so a
  // card is expanded when its default differs from whether the user toggled
  // it (XOR). This sidesteps the "useState initializer runs once with an empty
  // messages prop" trap (the viewer mounts while loading) without a
  // setState-in-effect, and the most-recent card opens as soon as the chain
  // arrives.
  const [toggledIds, setToggledIds] = useState<Set<string>>(() => new Set());

  const isExpanded = (key: string): boolean => {
    const defaultExpanded = key === lastKey;
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

  const last = messages.length > 0 ? messages[messages.length - 1] : null;

  let body: React.ReactNode;
  if (loading) {
    body = (
      <div className="flex h-[60vh] items-center justify-center">
        <Spinner />
      </div>
    );
  } else if (error) {
    body = (
      <div className="flex h-[60vh] items-center justify-center px-6 text-center text-sm text-red-600">
        {error.message}
      </div>
    );
  } else if (messages.length === 0) {
    body = (
      <div className="flex h-[40vh] items-center justify-center text-sm text-zinc-400">
        {t('conversation.empty')}
      </div>
    );
  } else {
    body = (
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
    );
  }

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
      <div className="flex flex-1 flex-col overflow-auto bg-[#F9FAFB]">{body}</div>
    </Modal>
  );
}
