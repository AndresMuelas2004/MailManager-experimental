import { useMemo, useState } from 'react';
import { ChevronDown, ChevronRight, Plus, X } from 'lucide-react';

import { useTranslation } from '../../../lib/i18n';
import type {
  AccountOut,
  MailboxOut,
  VirtualMailboxCreate,
  VirtualMailboxFilterBox,
  VirtualMailboxFilterPayload,
  VirtualMailboxOut,
} from '../../../api/types/dto';

type Props = {
  initial?: VirtualMailboxOut;
  mailboxes: MailboxOut[];
  accounts: AccountOut[];
  saving: boolean;
  submitLabel?: string;
  onSubmit: (payload: VirtualMailboxCreate) => Promise<void>;
  onCancel: () => void;
};

type FilterDraft = {
  box: VirtualMailboxFilterBox | '';
  from_email: string;
  subject_contains: string;
  is_read: '' | 'true' | 'false';
  is_favorite: '' | 'true' | 'false';
};

const EMPTY_FILTER: FilterDraft = {
  box: '',
  from_email: '',
  subject_contains: '',
  is_read: '',
  is_favorite: '',
};

const BOX_OPTIONS: Array<{ value: VirtualMailboxFilterBox; labelKey: string }> = [
  { value: 'ALL_MAIL', labelKey: 'vmboxForm.boxAllMail' },
  { value: 'SENT', labelKey: 'vmboxForm.boxSent' },
  { value: 'SPAM', labelKey: 'vmboxForm.boxSpam' },
  { value: 'TRASH', labelKey: 'vmboxForm.boxTrash' },
];

function pickFilter(filter: FilterDraft): VirtualMailboxFilterPayload {
  const out: VirtualMailboxFilterPayload = {};
  if (filter.box) out.box = filter.box;
  if (filter.from_email.trim()) out.from_email = filter.from_email.trim();
  if (filter.subject_contains.trim()) out.subject_contains = filter.subject_contains.trim();
  if (filter.is_read !== '') out.is_read = filter.is_read === 'true';
  if (filter.is_favorite !== '') out.is_favorite = filter.is_favorite === 'true';
  return out;
}

function readInitialFilter(raw: Record<string, unknown> | undefined): FilterDraft {
  if (!raw) return EMPTY_FILTER;
  const result: FilterDraft = { ...EMPTY_FILTER };
  if (typeof raw.box === 'string') result.box = raw.box as VirtualMailboxFilterBox;
  if (typeof raw.from_email === 'string') result.from_email = raw.from_email;
  if (typeof raw.subject_contains === 'string') result.subject_contains = raw.subject_contains;
  if (typeof raw.is_read === 'boolean') result.is_read = raw.is_read ? 'true' : 'false';
  if (typeof raw.is_favorite === 'boolean') result.is_favorite = raw.is_favorite ? 'true' : 'false';
  return result;
}

function describeAccount(account: AccountOut): string {
  return account.email_address ?? account.display_label;
}

export default function VirtualMailboxForm({
  initial,
  mailboxes,
  accounts,
  saving,
  submitLabel,
  onSubmit,
  onCancel,
}: Props) {
  const { t } = useTranslation();
  const [name, setName] = useState(initial?.display_name ?? '');
  const [selectedAccountIds, setSelectedAccountIds] = useState<string[]>(
    () => initial?.account_ids ?? [],
  );
  const [expandedMailboxes, setExpandedMailboxes] = useState<Set<string>>(() => new Set());
  const [filter, setFilter] = useState<FilterDraft>(() =>
    readInitialFilter(initial?.filter_payload as Record<string, unknown> | undefined),
  );
  const [submitError, setSubmitError] = useState<string | null>(null);

  const accountsByMailbox = useMemo(() => {
    const map = new Map<string, AccountOut[]>();
    for (const account of accounts) {
      const list = map.get(account.mailbox_id) ?? [];
      list.push(account);
      map.set(account.mailbox_id, list);
    }
    return map;
  }, [accounts]);

  const accountsById = useMemo(() => {
    const map = new Map<string, AccountOut>();
    for (const account of accounts) map.set(account.account_id, account);
    return map;
  }, [accounts]);

  const sortedMailboxes = useMemo(
    () => [...mailboxes].sort((a, b) => (a.display_name ?? '').localeCompare(b.display_name ?? '')),
    [mailboxes],
  );

  const selectedSet = useMemo(() => new Set(selectedAccountIds), [selectedAccountIds]);

  const handleToggleMailbox = (mailboxId: string) => {
    setExpandedMailboxes((prev) => {
      const next = new Set(prev);
      if (next.has(mailboxId)) next.delete(mailboxId);
      else next.add(mailboxId);
      return next;
    });
  };

  const handleAddAccount = (accountId: string) => {
    setSelectedAccountIds((prev) => (prev.includes(accountId) ? prev : [...prev, accountId]));
  };

  const handleRemoveAccount = (accountId: string) => {
    setSelectedAccountIds((prev) => prev.filter((id) => id !== accountId));
  };

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    setSubmitError(null);
    const trimmedName = name.trim();
    if (trimmedName.length === 0) {
      setSubmitError(t('vmboxForm.errorNameRequired'));
      return;
    }
    if (selectedAccountIds.length === 0) {
      setSubmitError(t('vmboxForm.errorAccountRequired'));
      return;
    }
    const payload: VirtualMailboxCreate = {
      display_name: trimmedName,
      account_ids: selectedAccountIds,
      filter_payload: pickFilter(filter),
    };
    try {
      await onSubmit(payload);
    } catch (err) {
      const message = err instanceof Error ? err.message : t('vmboxForm.errorSaveFailed');
      setSubmitError(message);
    }
  };

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-6">
      <div className="flex flex-col gap-2">
        <label htmlFor="vmb-name" className="text-[13px] font-semibold text-zinc-700">
          {t('vmboxForm.nameLabel')}
        </label>
        <input
          id="vmb-name"
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          maxLength={120}
          required
          placeholder={t('vmboxForm.namePlaceholder')}
          className="rounded-md border border-zinc-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none"
        />
      </div>

      <fieldset className="flex flex-col gap-2">
        <legend className="text-[13px] font-semibold text-zinc-700">
          {t('vmboxForm.accountsLegend')}
        </legend>
        <p className="text-xs text-zinc-500">{t('vmboxForm.accountsHelp')}</p>

        {/* Chips de cuentas seleccionadas */}
        <div className="flex min-h-[36px] flex-wrap gap-2 rounded-md border border-dashed border-zinc-300 bg-zinc-50 p-2">
          {selectedAccountIds.length === 0 ? (
            <span className="text-xs text-zinc-400">{t('vmboxForm.noAccountsAdded')}</span>
          ) : (
            selectedAccountIds.map((aid) => {
              const account = accountsById.get(aid);
              const label = account ? describeAccount(account) : aid;
              const provider = account?.provider;
              return (
                <span
                  key={aid}
                  className="inline-flex items-center gap-1.5 rounded-full bg-blue-100 px-2.5 py-1 text-xs text-blue-800"
                >
                  <span className="truncate">{label}</span>
                  {provider && <span className="text-blue-500">({provider})</span>}
                  <button
                    type="button"
                    onClick={() => handleRemoveAccount(aid)}
                    aria-label={t('vmboxForm.removeAccount', { label })}
                    className="grid h-4 w-4 place-items-center rounded-full text-blue-600 hover:bg-blue-200"
                  >
                    <X className="h-3 w-3" />
                  </button>
                </span>
              );
            })
          )}
        </div>

        {/* Acordeón por bandeja real */}
        <div className="mt-1 max-h-72 overflow-auto rounded-md border border-zinc-300 text-sm">
          {sortedMailboxes.length === 0 ? (
            <div className="px-3 py-3 text-xs text-zinc-500">
              {t('vmboxForm.noMailboxesConnected')}
            </div>
          ) : (
            sortedMailboxes.map((mailbox) => {
              const accs = accountsByMailbox.get(mailbox.mailbox_id) ?? [];
              const expanded = expandedMailboxes.has(mailbox.mailbox_id);
              return (
                <div key={mailbox.mailbox_id} className="border-b border-zinc-100 last:border-b-0">
                  <button
                    type="button"
                    onClick={() => handleToggleMailbox(mailbox.mailbox_id)}
                    className="flex w-full items-center gap-2 px-3 py-2 text-left hover:bg-zinc-50"
                  >
                    {expanded ? (
                      <ChevronDown className="h-4 w-4 text-zinc-500" />
                    ) : (
                      <ChevronRight className="h-4 w-4 text-zinc-500" />
                    )}
                    <span className="font-medium text-zinc-800">
                      {mailbox.display_name ?? mailbox.mailbox_id}
                    </span>
                    <span className="text-xs text-zinc-400">
                      {accs.length === 1
                        ? t('vmboxForm.accountsCountOne', { count: accs.length })
                        : t('vmboxForm.accountsCountMany', { count: accs.length })}
                    </span>
                  </button>
                  {expanded && (
                    <div className="flex flex-col">
                      {accs.length === 0 ? (
                        <div className="px-9 pb-2 text-xs text-zinc-400">
                          {t('vmboxForm.mailboxNoAccounts')}
                        </div>
                      ) : (
                        accs.map((account) => {
                          const alreadySelected = selectedSet.has(account.account_id);
                          return (
                            <button
                              key={account.account_id}
                              type="button"
                              onClick={() =>
                                alreadySelected ? undefined : handleAddAccount(account.account_id)
                              }
                              disabled={alreadySelected}
                              className="flex items-center gap-2 px-9 py-1.5 text-left hover:bg-zinc-50 disabled:opacity-60 disabled:hover:bg-transparent"
                            >
                              {alreadySelected ? (
                                <span className="text-xs text-zinc-400">
                                  {t('vmboxForm.added')}
                                </span>
                              ) : (
                                <Plus className="h-3.5 w-3.5 text-blue-600" />
                              )}
                              <span className="truncate text-sm text-zinc-700">
                                {describeAccount(account)}
                              </span>
                              <span className="text-xs text-zinc-400">({account.provider})</span>
                            </button>
                          );
                        })
                      )}
                    </div>
                  )}
                </div>
              );
            })
          )}
        </div>
      </fieldset>

      <fieldset className="flex flex-col gap-3">
        <legend className="text-[13px] font-semibold text-zinc-700">
          {t('vmboxForm.filtersLegend')}
        </legend>
        <p className="text-xs text-zinc-500">{t('vmboxForm.filtersHelp')}</p>

        <label className="flex flex-col gap-1 text-sm">
          <span className="text-xs font-medium text-zinc-600">{t('vmboxForm.folderLabel')}</span>
          <select
            value={filter.box}
            onChange={(e) =>
              setFilter((prev) => ({
                ...prev,
                box: e.target.value as VirtualMailboxFilterBox | '',
              }))
            }
            className="rounded-md border border-zinc-300 px-2 py-1.5 text-sm"
          >
            <option value="">{t('vmboxForm.folderAny')}</option>
            {BOX_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {t(opt.labelKey)}
              </option>
            ))}
          </select>
        </label>

        <label className="flex flex-col gap-1 text-sm">
          <span className="text-xs font-medium text-zinc-600">{t('vmboxForm.fromExactLabel')}</span>
          <input
            type="text"
            value={filter.from_email}
            onChange={(e) => setFilter((prev) => ({ ...prev, from_email: e.target.value }))}
            placeholder={t('vmboxForm.fromExactPlaceholder')}
            className="rounded-md border border-zinc-300 px-2 py-1.5 text-sm"
          />
        </label>

        <label className="flex flex-col gap-1 text-sm">
          <span className="text-xs font-medium text-zinc-600">
            {t('vmboxForm.subjectContainsLabel')}
          </span>
          <input
            type="text"
            value={filter.subject_contains}
            onChange={(e) => setFilter((prev) => ({ ...prev, subject_contains: e.target.value }))}
            placeholder={t('vmboxForm.subjectContainsPlaceholder')}
            className="rounded-md border border-zinc-300 px-2 py-1.5 text-sm"
          />
        </label>

        <div className="flex flex-col gap-3 sm:flex-row">
          <label className="flex flex-1 flex-col gap-1 text-sm">
            <span className="text-xs font-medium text-zinc-600">{t('vmboxForm.readLabel')}</span>
            <select
              value={filter.is_read}
              onChange={(e) =>
                setFilter((prev) => ({
                  ...prev,
                  is_read: e.target.value as FilterDraft['is_read'],
                }))
              }
              className="rounded-md border border-zinc-300 px-2 py-1.5 text-sm"
            >
              <option value="">{t('vmboxForm.readAny')}</option>
              <option value="false">{t('vmboxForm.readUnreadOnly')}</option>
              <option value="true">{t('vmboxForm.readReadOnly')}</option>
            </select>
          </label>

          <label className="flex flex-1 flex-col gap-1 text-sm">
            <span className="text-xs font-medium text-zinc-600">
              {t('vmboxForm.favoriteLabel')}
            </span>
            <select
              value={filter.is_favorite}
              onChange={(e) =>
                setFilter((prev) => ({
                  ...prev,
                  is_favorite: e.target.value as FilterDraft['is_favorite'],
                }))
              }
              className="rounded-md border border-zinc-300 px-2 py-1.5 text-sm"
            >
              <option value="">{t('vmboxForm.favoriteAny')}</option>
              <option value="true">{t('vmboxForm.favoriteOnly')}</option>
              <option value="false">{t('vmboxForm.favoriteExclude')}</option>
            </select>
          </label>
        </div>
      </fieldset>

      {submitError && (
        <div className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">{submitError}</div>
      )}

      <div className="flex flex-wrap items-center justify-end gap-2">
        <button
          type="button"
          onClick={onCancel}
          className="rounded-md border border-zinc-300 bg-white px-3 py-1.5 text-sm font-medium text-zinc-700 hover:bg-zinc-50"
        >
          {t('vmboxForm.cancel')}
        </button>
        <button
          type="submit"
          disabled={saving}
          className="rounded-md bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-60"
        >
          {saving ? t('common.saving') : (submitLabel ?? t('vmboxForm.save'))}
        </button>
      </div>
    </form>
  );
}
