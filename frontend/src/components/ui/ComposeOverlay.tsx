import { useRef, useState } from 'react';
import { Paperclip, Save, Send, X, ChevronDown } from 'lucide-react';

import Spinner from '../common/Spinner';
import AttachmentChip, { type ComposerAttachmentChipDisplay } from './AttachmentChip';
import RichTextEditor from './RichTextEditor';
import RecipientAutocompleteInput from './RecipientAutocompleteInput';
import { getProviderMeta } from '../../lib/providers';
import { MAX_MESSAGE_SIZE, formatBytes } from '../../lib/attachments';
import { useTranslation } from '../../lib/i18n';
import type { ComposerMode } from '../../lib/types';
import type { UiError } from '../../api/client/errors';
import type { AccountOut, ContactSuggestion } from '../../api/types/dto';

type ComposeAccount = Pick<
  AccountOut,
  'account_id' | 'provider' | 'email_address' | 'display_label'
>;

type Props = {
  mode: ComposerMode;
  accounts: ComposeAccount[];
  selectedAccountId: string;
  onSelectedAccountChange: (id: string) => void;
  to: string;
  onToChange: (v: string) => void;
  cc: string;
  onCcChange: (v: string) => void;
  bcc: string;
  onBccChange: (v: string) => void;
  subject: string;
  onSubjectChange: (v: string) => void;
  body: string;
  onBodyChange: (v: string) => void;
  sending: boolean;
  saving: boolean;
  error: UiError | null;
  recipientError: UiError | null;
  bodyError: UiError | null;
  canSendEmail: boolean;
  canSaveDraft: boolean;
  canSendDraft: boolean;
  onSendEmail: () => void;
  onSaveDraft: () => void;
  onSendDraft: () => void;
  onClose: () => void;
  // Attachments — D-25. ``attachmentsEnabled`` is true while the
  // composer is open with an account selected, in any of the three
  // modes. The first attached file in ``new_email`` / ``new_draft``
  // bootstraps a silent draft on the provider so subsequent uploads
  // have a real ``provider_draft_id`` to bind to (handled in the hook).
  // ``accountSelectorLocked`` mirrors that bootstrap: once a draft
  // exists, the account dropdown is disabled to avoid orphaning the
  // attachments on the original account.
  attachmentsEnabled: boolean;
  accountSelectorLocked: boolean;
  attachmentChips: ComposerAttachmentChipDisplay[];
  attachmentTotalSize: number;
  onAddFiles: (files: File[]) => void;
  onRemoveAttachment: (chipId: string) => void;
  // Recipient autocomplete. The same suggestions / loading feed all three
  // recipient fields; only the focused field shows its dropdown, and each
  // instance filters out addresses already present in its own ``value``.
  // ``onRecipientQueryChange`` reports the active fragment of whichever field
  // the user is editing so the host can debounce + fetch.
  recipientSuggestions: ContactSuggestion[];
  recipientSuggestionsLoading: boolean;
  onRecipientQueryChange: (fragment: string) => void;
};

const TITLE_KEY_BY_MODE: Record<ComposerMode, string> = {
  new_email: 'composer.titleNewEmail',
  new_draft: 'composer.titleNewDraft',
  edit_draft: 'composer.titleEditDraft',
  reply: 'composer.titleReply',
  reply_all: 'composer.titleReplyAll',
  forward: 'composer.titleForward',
};

export default function ComposeOverlay({
  mode,
  accounts,
  selectedAccountId,
  onSelectedAccountChange,
  to,
  onToChange,
  cc,
  onCcChange,
  bcc,
  onBccChange,
  subject,
  onSubjectChange,
  body,
  onBodyChange,
  sending,
  saving,
  error,
  recipientError,
  bodyError,
  canSendEmail,
  canSaveDraft,
  canSendDraft,
  onSendEmail,
  onSaveDraft,
  onSendDraft,
  onClose,
  attachmentsEnabled,
  accountSelectorLocked,
  attachmentChips,
  attachmentTotalSize,
  onAddFiles,
  onRemoveAttachment,
  recipientSuggestions,
  recipientSuggestionsLoading,
  onRecipientQueryChange,
}: Props) {
  const { t } = useTranslation();
  const [selectorOpen, setSelectorOpen] = useState(false);
  const [ccBccOpen, setCcBccOpen] = useState(() => cc.trim().length > 0 || bcc.trim().length > 0);
  const [dragActive, setDragActive] = useState(false);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const handleDragOver = (e: React.DragEvent<HTMLDivElement>) => {
    if (!e.dataTransfer.types.includes('Files')) return;
    e.preventDefault();
    if (attachmentsEnabled) setDragActive(true);
  };
  const handleDragLeave = (e: React.DragEvent<HTMLDivElement>) => {
    if (e.currentTarget === e.target) setDragActive(false);
  };
  const handleDrop = (e: React.DragEvent<HTMLDivElement>) => {
    if (!e.dataTransfer.types.includes('Files')) return;
    e.preventDefault();
    setDragActive(false);
    if (!attachmentsEnabled) return;
    const files = Array.from(e.dataTransfer.files || []);
    if (files.length > 0) onAddFiles(files);
  };
  const handleFileInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files || []);
    if (files.length > 0) onAddFiles(files);
    // Reset so picking the same file twice in a row still triggers onChange.
    e.target.value = '';
  };

  const sizeOverLimit = attachmentTotalSize > MAX_MESSAGE_SIZE;

  const selectedAccount = accounts.find((a) => a.account_id === selectedAccountId);
  const accountLabel = (a: ComposeAccount) => a.email_address ?? a.display_label;
  const title = t(TITLE_KEY_BY_MODE[mode]);

  const showSendEmail = mode === 'new_email';
  const showSaveDraft =
    mode === 'new_draft' ||
    mode === 'edit_draft' ||
    mode === 'reply' ||
    mode === 'reply_all' ||
    mode === 'forward';
  const showSendDraft =
    mode === 'edit_draft' || mode === 'reply' || mode === 'reply_all' || mode === 'forward';

  return (
    <div
      className="fixed inset-0 z-50 flex h-[100dvh] w-full flex-col bg-white lg:inset-auto lg:right-6 lg:bottom-0 lg:h-auto lg:w-[400px] lg:rounded-t-2xl lg:border lg:border-zinc-200 lg:shadow-xl"
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
    >
      {dragActive && attachmentsEnabled ? (
        <div className="pointer-events-none absolute inset-0 z-10 flex items-center justify-center rounded-t-2xl border-2 border-dashed border-blue-400 bg-blue-50/80 text-[14px] font-medium text-blue-700">
          {t('composer.dropToAttach')}
        </div>
      ) : null}
      <div className="flex items-center justify-between px-5 pt-5 pb-3">
        <h3 className="text-base font-semibold text-zinc-900">{title}</h3>
        <button
          type="button"
          onClick={onClose}
          className="text-zinc-500 hover:text-zinc-700"
          aria-label={t('common.close')}
        >
          <X className="h-5 w-5" />
        </button>
      </div>

      <div className="flex flex-1 flex-col gap-4 overflow-y-auto px-5 pb-5 lg:flex-none lg:overflow-visible">
        <div className="relative">
          {!ccBccOpen && (
            <button
              type="button"
              onClick={() => setCcBccOpen(true)}
              className="absolute top-0 right-0 z-10 text-xs font-medium text-blue-600 hover:text-blue-700"
            >
              {t('composer.addCcBcc')}
            </button>
          )}
          <RecipientAutocompleteInput
            label={t('composer.fieldTo')}
            value={to}
            onChange={onToChange}
            placeholder={t('composer.toPlaceholder')}
            suggestions={recipientSuggestions}
            loading={recipientSuggestionsLoading}
            onQueryChange={onRecipientQueryChange}
          />
        </div>

        {ccBccOpen && (
          <>
            <RecipientAutocompleteInput
              label={t('composer.fieldCc')}
              value={cc}
              onChange={onCcChange}
              placeholder={t('composer.ccPlaceholder')}
              suggestions={recipientSuggestions}
              loading={recipientSuggestionsLoading}
              onQueryChange={onRecipientQueryChange}
            />
            <RecipientAutocompleteInput
              label={t('composer.fieldBcc')}
              value={bcc}
              onChange={onBccChange}
              placeholder={t('composer.bccPlaceholder')}
              suggestions={recipientSuggestions}
              loading={recipientSuggestionsLoading}
              onQueryChange={onRecipientQueryChange}
            />
          </>
        )}

        <div className="relative flex flex-col gap-1.5">
          <label className="text-sm font-medium text-zinc-900">{t('composer.sourceLabel')}</label>
          <button
            type="button"
            onClick={() => setSelectorOpen((v) => !v)}
            disabled={accountSelectorLocked}
            className="flex h-10 items-center justify-between rounded-[10px] bg-zinc-100 px-3 text-sm text-zinc-900 disabled:opacity-70"
          >
            <span>
              {selectedAccount ? accountLabel(selectedAccount) : t('composer.selectAccount')}
            </span>
            {!accountSelectorLocked && <ChevronDown className="h-4 w-4 text-zinc-500" />}
          </button>
          {selectorOpen && !accountSelectorLocked && (
            <div className="absolute top-full left-0 z-10 mt-1 w-full rounded-[10px] border border-zinc-200 bg-white py-1 shadow-lg">
              {accounts.map((a) => (
                <button
                  key={a.account_id}
                  type="button"
                  onClick={() => {
                    onSelectedAccountChange(a.account_id);
                    setSelectorOpen(false);
                  }}
                  className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm hover:bg-zinc-50"
                >
                  <span
                    className={`inline-block h-2 w-2 rounded-full ${getProviderMeta(a.provider).dotClass}`}
                  />
                  <span className="flex-1 text-zinc-900">{accountLabel(a)}</span>
                  {a.account_id === selectedAccountId && (
                    <span className="text-xs text-blue-600">&#10003;</span>
                  )}
                </button>
              ))}
            </div>
          )}
        </div>

        <div className="flex flex-col gap-1.5">
          <label className="text-sm font-medium text-zinc-900">{t('composer.subjectLabel')}</label>
          <input
            type="text"
            value={subject}
            onChange={(e) => onSubjectChange(e.target.value)}
            placeholder={t('composer.subjectPlaceholder')}
            className="h-10 rounded-[10px] border-[1.5px] border-zinc-200 px-3 text-sm text-zinc-900 placeholder:text-zinc-400 focus:border-blue-600 focus:outline-none"
          />
        </div>

        {attachmentsEnabled && attachmentChips.length > 0 ? (
          <div className="flex flex-wrap gap-1.5">
            {attachmentChips.map((chip) => (
              <AttachmentChip
                key={chip.id}
                chip={chip}
                onRemove={() => onRemoveAttachment(chip.id)}
              />
            ))}
          </div>
        ) : null}

        <div className="flex flex-1 flex-col gap-1.5">
          <label className="text-sm font-medium text-zinc-900">{t('composer.messageLabel')}</label>
          {/* Body is HTML now: RichTextEditor.onChange already hands back the
              HTML string (no event adapter). Safe-rendering rationale lives in
              RichTextEditor (no dangerouslySetInnerHTML; backend re-sanitises). */}
          <RichTextEditor
            value={body}
            onChange={onBodyChange}
            placeholder={t('composer.messagePlaceholder')}
            disabled={sending || saving}
            ariaLabel={t('composer.messageAria')}
          />
        </div>

        {recipientError && (
          <p className="text-center text-sm text-red-600">{recipientError.message}</p>
        )}
        {bodyError && <p className="text-center text-sm text-red-600">{bodyError.message}</p>}
        {error && <p className="text-center text-sm text-red-600">{error.message}</p>}

        {attachmentsEnabled ? (
          <div className="flex items-center justify-between text-[12px]">
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              className="inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-zinc-600 hover:bg-zinc-100 hover:text-zinc-900"
            >
              <Paperclip className="h-4 w-4" />
              <span>{t('composer.attach')}</span>
            </button>
            <span
              className={[
                'tabular-nums',
                sizeOverLimit ? 'text-red-600 font-medium' : 'text-zinc-500',
              ].join(' ')}
              aria-label={t('composer.totalSizeAria')}
            >
              {t('composer.sizeOfLimit', { size: formatBytes(attachmentTotalSize) })}
            </span>
            <input
              ref={fileInputRef}
              type="file"
              multiple
              className="hidden"
              onChange={handleFileInputChange}
            />
          </div>
        ) : null}

        <div className="flex flex-col gap-2">
          {showSendEmail && (
            <button
              type="button"
              disabled={!canSendEmail}
              onClick={onSendEmail}
              className="flex h-11 items-center justify-center gap-2 rounded-xl bg-blue-600 text-sm font-semibold text-white shadow-lg shadow-blue-600/25 transition-colors hover:bg-blue-700 disabled:opacity-50"
            >
              {sending ? (
                <Spinner size="sm" color="white" />
              ) : (
                <>
                  <Send className="h-[18px] w-[18px]" />
                  {t('composer.send')}
                </>
              )}
            </button>
          )}

          {showSendDraft && (
            <button
              type="button"
              disabled={!canSendDraft}
              onClick={onSendDraft}
              className="flex h-11 items-center justify-center gap-2 rounded-xl bg-blue-600 text-sm font-semibold text-white shadow-lg shadow-blue-600/25 transition-colors hover:bg-blue-700 disabled:opacity-50"
            >
              {sending ? (
                <Spinner size="sm" color="white" />
              ) : (
                <>
                  <Send className="h-[18px] w-[18px]" />
                  {t('composer.sendDraft')}
                </>
              )}
            </button>
          )}

          {showSaveDraft && (
            <button
              type="button"
              disabled={!canSaveDraft}
              onClick={onSaveDraft}
              className="flex h-11 items-center justify-center gap-2 rounded-xl border-[1.5px] border-zinc-200 bg-white text-sm font-semibold text-zinc-900 transition-colors hover:bg-zinc-50 disabled:opacity-50"
            >
              {saving ? (
                <Spinner size="sm" />
              ) : (
                <>
                  <Save className="h-[18px] w-[18px]" />
                  {t('composer.save')}
                </>
              )}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
