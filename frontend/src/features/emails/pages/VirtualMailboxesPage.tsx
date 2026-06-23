import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { Filter, Pencil, Plus, Trash2 } from 'lucide-react';

import useVirtualMailboxes from '../hooks/useVirtualMailboxes';
import useAccountPickerData from '../hooks/useAccountPickerData';
import VirtualMailboxForm from '../components/VirtualMailboxForm';
import Modal from '../../../components/common/Modal';
import Spinner from '../../../components/common/Spinner';
import { useTranslation } from '../../../lib/i18n';
import type { Translate } from '../../../lib/i18n';
import type {
  AccountOut,
  VirtualMailboxCreate,
  VirtualMailboxOut,
  VirtualMailboxUpdate,
} from '../../../api/types/dto';

type EditorState =
  | { kind: 'closed' }
  | { kind: 'create' }
  | { kind: 'edit'; record: VirtualMailboxOut };

// Box → i18n key. Centralised so a future rename does not drift between the
// form (BOX_OPTIONS in VirtualMailboxForm.tsx) and the listing description.
const BOX_LABEL_KEYS: Record<string, string> = {
  ALL_MAIL: 'virtualMailboxes.boxAllMail',
  SENT: 'virtualMailboxes.boxSent',
  SPAM: 'virtualMailboxes.boxSpam',
  TRASH: 'virtualMailboxes.boxTrash',
};

function boxLabel(t: Translate, value: unknown): string {
  return typeof value === 'string' && BOX_LABEL_KEYS[value]
    ? t(BOX_LABEL_KEYS[value])
    : String(value ?? '');
}

function describeAccounts(
  t: Translate,
  record: VirtualMailboxOut,
  accounts: ReadonlyArray<AccountOut>,
): string {
  const ids = record.account_ids ?? [];
  if (ids.length === 0) return t('virtualMailboxes.noAccountsSelected');
  const labels = ids
    .map((aid) => accounts.find((a) => a.account_id === aid))
    .filter((a): a is AccountOut => Boolean(a))
    .map((a) => a.email_address ?? a.display_label);
  if (labels.length === 0)
    return ids.length === 1
      ? t('virtualMailboxes.accountsNoAccessOne', { count: ids.length })
      : t('virtualMailboxes.accountsNoAccessMany', { count: ids.length });
  if (labels.length <= 2) return labels.join(', ');
  return t('virtualMailboxes.moreAccounts', {
    labels: labels.slice(0, 2).join(', '),
    count: labels.length - 2,
  });
}

function describeFilter(t: Translate, record: VirtualMailboxOut): string {
  const fp = record.filter_payload ?? {};
  const parts: string[] = [];
  if (typeof fp.box === 'string') {
    parts.push(t('virtualMailboxes.filterIn', { label: boxLabel(t, fp.box) }));
  } else if (Array.isArray(fp.box_not_in) && fp.box_not_in.length > 0) {
    // ``box_not_in`` is the documented complement of ``box``. Without
    // it the description was silent about whether SPAM/TRASH were
    // excluded — making "de X" look like the only filter even when
    // the vmbox excluded entire boxes.
    const labels = (fp.box_not_in as unknown[]).map((b) => boxLabel(t, b)).join(', ');
    parts.push(t('virtualMailboxes.filterOutOf', { labels }));
  }
  if (typeof fp.from_email === 'string')
    parts.push(t('virtualMailboxes.filterFrom', { email: fp.from_email }));
  if (typeof fp.subject_contains === 'string')
    parts.push(t('virtualMailboxes.filterSubject', { value: fp.subject_contains }));
  if (typeof fp.is_read === 'boolean')
    parts.push(fp.is_read ? t('virtualMailboxes.filterRead') : t('virtualMailboxes.filterUnread'));
  if (typeof fp.is_favorite === 'boolean')
    parts.push(
      fp.is_favorite
        ? t('virtualMailboxes.filterFavorite')
        : t('virtualMailboxes.filterNotFavorite'),
    );
  if (parts.length === 0) return t('virtualMailboxes.noFilters');
  return parts.join(' · ');
}

export default function VirtualMailboxesPage() {
  const { mailboxId } = useParams<{ mailboxId: string }>();
  const { t } = useTranslation();
  const list = useVirtualMailboxes();
  const picker = useAccountPickerData();
  const [editor, setEditor] = useState<EditorState>({ kind: 'closed' });

  const handleSubmit = async (payload: VirtualMailboxCreate) => {
    if (editor.kind === 'create') {
      await list.create(payload);
    } else if (editor.kind === 'edit') {
      const updatePayload: VirtualMailboxUpdate = payload;
      await list.update(editor.record.virtual_mailbox_id, updatePayload);
    }
    setEditor({ kind: 'closed' });
  };

  const handleDelete = async (record: VirtualMailboxOut) => {
    if (!window.confirm(t('virtualMailboxes.confirmDelete', { name: record.display_name }))) return;
    try {
      await list.remove(record.virtual_mailbox_id);
    } catch {
      /* surfaced through list.error */
    }
  };

  return (
    <div className="flex h-full flex-col">
      <div className="flex flex-col gap-4 px-4 pt-6 pb-6 sm:flex-row sm:items-start sm:justify-between lg:px-8 lg:pt-8">
        <div className="flex flex-col gap-1.5">
          <h1 className="text-2xl font-bold tracking-tight text-zinc-900 lg:text-[28px]">
            {t('virtualMailboxes.title')}
          </h1>
          <p className="text-[15px] leading-[1.5] text-zinc-500">
            {t('virtualMailboxes.subtitle')}
          </p>
        </div>
        <button
          type="button"
          onClick={() => setEditor({ kind: 'create' })}
          className="inline-flex items-center gap-2 rounded-md bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700"
        >
          <Plus className="h-4 w-4" />
          {t('virtualMailboxes.newButton')}
        </button>
      </div>

      {list.error && (
        <div className="mx-4 mb-4 rounded-md bg-red-50 px-3 py-2 text-sm text-red-700 lg:mx-8">
          {list.error.message}
        </div>
      )}

      {list.loading ? (
        <div className="flex h-64 items-center justify-center">
          <Spinner />
        </div>
      ) : list.virtualMailboxes.length === 0 ? (
        <div className="px-4 py-10 text-center text-sm text-zinc-400 lg:px-8">
          {t('virtualMailboxes.empty')}
        </div>
      ) : (
        <ul className="flex flex-col">
          {list.virtualMailboxes.map((record) => (
            <li
              key={record.virtual_mailbox_id}
              className="flex items-center gap-4 border-b border-zinc-100 px-4 py-4 hover:bg-zinc-50 lg:px-8"
            >
              <Filter className="h-5 w-5 shrink-0 text-zinc-400" />
              <div className="flex flex-1 flex-col gap-0.5">
                <Link
                  to={`/m/${mailboxId}/virtual-mailboxes/${record.virtual_mailbox_id}`}
                  className="text-sm font-semibold text-zinc-900 hover:text-blue-700"
                >
                  {record.display_name}
                </Link>
                <div className="text-xs text-zinc-500">
                  {describeAccounts(t, record, picker.accounts)} · {describeFilter(t, record)}
                </div>
              </div>
              <button
                type="button"
                onClick={() => setEditor({ kind: 'edit', record })}
                className="grid h-8 w-8 place-items-center rounded-md text-zinc-500 hover:bg-zinc-100 hover:text-zinc-700"
                aria-label={t('virtualMailboxes.editAria')}
              >
                <Pencil className="h-4 w-4" />
              </button>
              <button
                type="button"
                onClick={() => handleDelete(record)}
                disabled={list.mutating}
                className="grid h-8 w-8 place-items-center rounded-md text-red-500 hover:bg-red-50 disabled:opacity-50"
                aria-label={t('virtualMailboxes.deleteAria')}
              >
                <Trash2 className="h-4 w-4" />
              </button>
            </li>
          ))}
        </ul>
      )}

      <Modal
        open={editor.kind !== 'closed'}
        onClose={() => setEditor({ kind: 'closed' })}
        widthClass="max-w-2xl"
        mobileFullScreen
        ariaLabel={
          editor.kind === 'edit' ? t('virtualMailboxes.editTitle') : t('virtualMailboxes.newTitle')
        }
      >
        <div className="flex max-h-full flex-col overflow-auto px-4 pt-6 pb-6 lg:max-h-[80vh] lg:px-6">
          <h2 className="mb-4 text-lg font-semibold text-zinc-900">
            {editor.kind === 'edit'
              ? t('virtualMailboxes.editTitle')
              : t('virtualMailboxes.newTitle')}
          </h2>
          <VirtualMailboxForm
            initial={editor.kind === 'edit' ? editor.record : undefined}
            mailboxes={picker.mailboxes}
            accounts={picker.accounts}
            saving={list.mutating || picker.loading}
            submitLabel={
              editor.kind === 'edit'
                ? t('virtualMailboxes.saveChanges')
                : t('virtualMailboxes.create')
            }
            onSubmit={handleSubmit}
            onCancel={() => setEditor({ kind: 'closed' })}
          />
        </div>
      </Modal>
    </div>
  );
}
