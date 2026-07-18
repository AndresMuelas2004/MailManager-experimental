import { useMemo, useState } from 'react';
import { Loader2, Pencil, Play, Plus, Trash2 } from 'lucide-react';

import useRules from '../hooks/useRules';
import useFolders from '../hooks/useFolders';
import useRuleApplyStatus from '../hooks/useRuleApplyStatus';
import RuleForm from '../components/RuleForm';
import type { RuleFormValues } from '../components/RuleForm';
import Modal from '../../../components/common/Modal';
import Spinner from '../../../components/common/Spinner';
import { useTranslation } from '../../../lib/i18n';
import type { Translate } from '../../../lib/i18n';
import type { RuleCreate, RuleOut, RuleUpdate } from '../../../api/types/dto';

type EditorState = { kind: 'closed' } | { kind: 'create' } | { kind: 'edit'; record: RuleOut };

function describeConditions(t: Translate, rule: RuleOut): string {
  const parts: string[] = [];
  if (rule.match_from_email) parts.push(t('rules.condFrom', { email: rule.match_from_email }));
  if (rule.match_subject_contains)
    parts.push(t('rules.condSubject', { value: rule.match_subject_contains }));
  return parts.length > 0 ? parts.join(' · ') : t('rules.condNone');
}

export default function RulesSettingsPage() {
  const { t } = useTranslation();
  const rules = useRules();
  const foldersHook = useFolders();
  const [editor, setEditor] = useState<EditorState>({ kind: 'closed' });

  const folderNameById = useMemo(() => {
    const map = new Map<string, string>();
    for (const folder of foldersHook.folders) map.set(folder.folder_id, folder.name);
    return map;
  }, [foldersHook.folders]);

  const handleSubmit = async (values: RuleFormValues) => {
    if (editor.kind === 'create') {
      const payload: RuleCreate = {
        target_folder_id: values.targetFolderId,
        is_enabled: values.isEnabled,
        apply_to_existing: values.applyToExisting,
      };
      if (values.name.trim()) payload.name = values.name.trim();
      if (values.matchFromEmail.trim()) payload.match_from_email = values.matchFromEmail.trim();
      if (values.matchSubjectContains.trim())
        payload.match_subject_contains = values.matchSubjectContains.trim();
      await rules.create(payload);
    } else if (editor.kind === 'edit') {
      // On edit, send ``null`` to CLEAR an emptied condition (omitting it would
      // leave the stored value unchanged). The client guard already blocks
      // clearing BOTH conditions.
      const payload: RuleUpdate = {
        target_folder_id: values.targetFolderId,
        is_enabled: values.isEnabled,
        apply_to_existing: values.applyToExisting,
        name: values.name.trim() ? values.name.trim() : null,
        match_from_email: values.matchFromEmail.trim() ? values.matchFromEmail.trim() : null,
        match_subject_contains: values.matchSubjectContains.trim()
          ? values.matchSubjectContains.trim()
          : null,
      };
      await rules.update(editor.record.rule_id, payload);
    }
    setEditor({ kind: 'closed' });
  };

  const handleToggleEnabled = (rule: RuleOut) => {
    void rules.update(rule.rule_id, { is_enabled: !rule.is_enabled });
  };

  const handleDelete = async (rule: RuleOut) => {
    const label = rule.name ?? t('rules.untitled');
    if (!window.confirm(t('rules.confirmDelete', { name: label }))) return;
    try {
      await rules.remove(rule.rule_id);
    } catch {
      /* surfaced through rules.error */
    }
  };

  const handleApply = (rule: RuleOut) => {
    rules.apply(rule.rule_id).catch(() => {
      /* surfaced through rules.error */
    });
  };

  const combinedError = rules.error ?? foldersHook.error;

  return (
    <div className="flex h-full flex-col">
      <div className="flex flex-col gap-4 px-4 pt-6 pb-6 sm:flex-row sm:items-start sm:justify-between lg:px-8 lg:pt-8">
        <div className="flex flex-col gap-1.5">
          <h1 className="text-2xl font-bold tracking-tight text-zinc-900 lg:text-[28px]">
            {t('rules.title')}
          </h1>
          <p className="text-[15px] leading-[1.5] text-zinc-500">{t('rules.subtitle')}</p>
        </div>
        <button
          type="button"
          onClick={() => setEditor({ kind: 'create' })}
          className="inline-flex items-center gap-2 rounded-md bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700"
        >
          <Plus className="h-4 w-4" />
          {t('rules.newButton')}
        </button>
      </div>

      {combinedError && (
        <div className="mx-4 mb-4 rounded-md bg-red-50 px-3 py-2 text-sm text-red-700 lg:mx-8">
          {combinedError.message}
        </div>
      )}

      {rules.loading ? (
        <div className="flex h-64 items-center justify-center">
          <Spinner />
        </div>
      ) : rules.rules.length === 0 ? (
        <div className="px-4 py-10 text-center text-sm text-zinc-400 lg:px-8">
          {t('rules.empty')}
        </div>
      ) : (
        <ul className="flex flex-col">
          {rules.rules.map((rule) => (
            <RuleRow
              key={rule.rule_id}
              rule={rule}
              folderName={folderNameById.get(rule.target_folder_id) ?? t('rules.unknownFolder')}
              conditions={describeConditions(t, rule)}
              busy={rules.mutating}
              onEdit={() => setEditor({ kind: 'edit', record: rule })}
              onDelete={() => handleDelete(rule)}
              onToggleEnabled={() => handleToggleEnabled(rule)}
              onApply={() => handleApply(rule)}
            />
          ))}
        </ul>
      )}

      <Modal
        open={editor.kind !== 'closed'}
        onClose={() => setEditor({ kind: 'closed' })}
        widthClass="max-w-lg"
        mobileFullScreen
        ariaLabel={editor.kind === 'edit' ? t('rules.editTitle') : t('rules.newTitle')}
      >
        <div className="flex max-h-full flex-col overflow-auto px-4 pt-6 pb-6 lg:max-h-[80vh] lg:px-6">
          <h2 className="mb-4 text-lg font-semibold text-zinc-900">
            {editor.kind === 'edit' ? t('rules.editTitle') : t('rules.newTitle')}
          </h2>
          <RuleForm
            initial={editor.kind === 'edit' ? editor.record : undefined}
            folders={foldersHook.folders}
            saving={rules.mutating}
            submitLabel={editor.kind === 'edit' ? t('rules.saveChanges') : t('rules.create')}
            onSubmit={handleSubmit}
            onCancel={() => setEditor({ kind: 'closed' })}
          />
        </div>
      </Modal>
    </div>
  );
}

// Page-local orchestrating row: owns the per-rule apply-status poll (a fetch
// hook, so it lives in the pages layer, not in a shared presentational
// component — mirrors how ``MailboxLayoutPage`` factors ``MailboxShell``). The
// poll is idle unless this rule has an active "apply to existing" job.
type RuleRowProps = {
  rule: RuleOut;
  folderName: string;
  conditions: string;
  busy: boolean;
  onEdit: () => void;
  onDelete: () => void;
  onToggleEnabled: () => void;
  onApply: () => void;
};

function RuleRow({
  rule,
  folderName,
  conditions,
  busy,
  onEdit,
  onDelete,
  onToggleEnabled,
  onApply,
}: RuleRowProps) {
  const { t } = useTranslation();
  const apply = useRuleApplyStatus(rule.rule_id);

  return (
    <li className="flex flex-col gap-2 border-b border-zinc-100 px-4 py-4 hover:bg-zinc-50 lg:flex-row lg:items-center lg:gap-4 lg:px-8">
      <div className="flex flex-1 flex-col gap-0.5">
        <span className="text-sm font-semibold text-zinc-900">
          {rule.name ?? t('rules.untitled')}
        </span>
        <span className="text-xs text-zinc-500">{conditions}</span>
        <span className="text-xs text-zinc-500">
          {t('rules.targetLabel', { folder: folderName })}
        </span>
      </div>

      <label className="flex items-center gap-1.5 text-xs text-zinc-600">
        <input
          type="checkbox"
          checked={rule.is_enabled}
          onChange={onToggleEnabled}
          disabled={busy}
          className="h-4 w-4"
        />
        {rule.is_enabled ? t('rules.enabled') : t('rules.disabled')}
      </label>

      {apply.active ? (
        <span className="inline-flex items-center gap-1.5 text-xs font-medium text-blue-600">
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
          {t('rules.applying', { count: apply.processedCount })}
        </span>
      ) : (
        <button
          type="button"
          onClick={onApply}
          disabled={busy}
          className="inline-flex items-center gap-1.5 rounded-md border border-zinc-300 bg-white px-2.5 py-1 text-xs font-medium text-zinc-700 hover:bg-zinc-50 disabled:opacity-50"
        >
          <Play className="h-3.5 w-3.5" />
          {apply.status === 'completed' ? t('rules.applyAgain') : t('rules.applyToExisting')}
        </button>
      )}

      <div className="flex items-center gap-1">
        <button
          type="button"
          onClick={onEdit}
          className="grid h-8 w-8 place-items-center rounded-md text-zinc-500 hover:bg-zinc-100 hover:text-zinc-700"
          aria-label={t('rules.editAria')}
        >
          <Pencil className="h-4 w-4" />
        </button>
        <button
          type="button"
          onClick={onDelete}
          disabled={busy}
          className="grid h-8 w-8 place-items-center rounded-md text-red-500 hover:bg-red-50 disabled:opacity-50"
          aria-label={t('rules.deleteAria')}
        >
          <Trash2 className="h-4 w-4" />
        </button>
      </div>
    </li>
  );
}
