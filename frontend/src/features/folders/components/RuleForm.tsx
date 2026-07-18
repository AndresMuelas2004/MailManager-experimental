import { useState } from 'react';

import { useTranslation } from '../../../lib/i18n';
import type { FolderOut, RuleOut } from '../../../api/types/dto';

// Raw form values. The page maps these to ``RuleCreate`` (omitting empty
// conditions) or ``RuleUpdate`` (sending ``null`` to CLEAR an emptied
// condition) — the two shapes differ, so the form stays payload-agnostic.
export type RuleFormValues = {
  name: string;
  matchFromEmail: string;
  matchSubjectContains: string;
  targetFolderId: string;
  isEnabled: boolean;
  applyToExisting: boolean;
};

type Props = {
  initial?: RuleOut;
  folders: FolderOut[];
  saving: boolean;
  submitLabel?: string;
  onSubmit: (values: RuleFormValues) => Promise<void>;
  onCancel: () => void;
};

export default function RuleForm({
  initial,
  folders,
  saving,
  submitLabel,
  onSubmit,
  onCancel,
}: Props) {
  const { t } = useTranslation();
  const [name, setName] = useState(initial?.name ?? '');
  const [matchFromEmail, setMatchFromEmail] = useState(initial?.match_from_email ?? '');
  const [matchSubjectContains, setMatchSubjectContains] = useState(
    initial?.match_subject_contains ?? '',
  );
  const [targetFolderId, setTargetFolderId] = useState(
    initial?.target_folder_id ?? folders[0]?.folder_id ?? '',
  );
  const [isEnabled, setIsEnabled] = useState(initial?.is_enabled ?? true);
  const [applyToExisting, setApplyToExisting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  const noFolders = folders.length === 0;

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    setSubmitError(null);
    // Client-side ≥1 condition guard: the backend "≥1 condition" 422 on
    // create/patch is a Pydantic ``{"detail":[...]}`` envelope (code
    // ``http_error``), not the ``{"error":{code}}`` service envelope, so the
    // raw message would not read well — validate here instead.
    if (matchFromEmail.trim().length === 0 && matchSubjectContains.trim().length === 0) {
      setSubmitError(t('ruleForm.errorConditionRequired'));
      return;
    }
    if (targetFolderId.length === 0) {
      setSubmitError(t('ruleForm.errorFolderRequired'));
      return;
    }
    try {
      await onSubmit({
        name,
        matchFromEmail,
        matchSubjectContains,
        targetFolderId,
        isEnabled,
        applyToExisting,
      });
    } catch (err) {
      const message = err instanceof Error ? err.message : t('ruleForm.errorSaveFailed');
      setSubmitError(message);
    }
  };

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-6">
      <div className="flex flex-col gap-2">
        <label htmlFor="rule-name" className="text-[13px] font-semibold text-zinc-700">
          {t('ruleForm.nameLabel')}
        </label>
        <input
          id="rule-name"
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          maxLength={120}
          placeholder={t('ruleForm.namePlaceholder')}
          className="rounded-md border border-zinc-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none"
        />
      </div>

      <fieldset className="flex flex-col gap-3">
        <legend className="text-[13px] font-semibold text-zinc-700">
          {t('ruleForm.conditionsLegend')}
        </legend>
        <p className="text-xs text-zinc-500">{t('ruleForm.conditionsHelp')}</p>

        <label className="flex flex-col gap-1 text-sm">
          <span className="text-xs font-medium text-zinc-600">{t('ruleForm.fromLabel')}</span>
          <input
            type="text"
            value={matchFromEmail}
            onChange={(e) => setMatchFromEmail(e.target.value)}
            placeholder={t('ruleForm.fromPlaceholder')}
            className="rounded-md border border-zinc-300 px-2 py-1.5 text-sm"
          />
        </label>

        <label className="flex flex-col gap-1 text-sm">
          <span className="text-xs font-medium text-zinc-600">{t('ruleForm.subjectLabel')}</span>
          <input
            type="text"
            value={matchSubjectContains}
            onChange={(e) => setMatchSubjectContains(e.target.value)}
            placeholder={t('ruleForm.subjectPlaceholder')}
            className="rounded-md border border-zinc-300 px-2 py-1.5 text-sm"
          />
        </label>
      </fieldset>

      <div className="flex flex-col gap-2">
        <label htmlFor="rule-folder" className="text-[13px] font-semibold text-zinc-700">
          {t('ruleForm.targetLabel')}
        </label>
        {noFolders ? (
          <p className="rounded-md bg-amber-50 px-3 py-2 text-sm text-amber-700">
            {t('ruleForm.noFolders')}
          </p>
        ) : (
          <select
            id="rule-folder"
            value={targetFolderId}
            onChange={(e) => setTargetFolderId(e.target.value)}
            className="rounded-md border border-zinc-300 px-2 py-2 text-sm"
          >
            {folders.map((folder) => (
              <option key={folder.folder_id} value={folder.folder_id}>
                {folder.name}
              </option>
            ))}
          </select>
        )}
      </div>

      <label className="flex items-center gap-2 text-sm text-zinc-700">
        <input
          type="checkbox"
          checked={isEnabled}
          onChange={(e) => setIsEnabled(e.target.checked)}
          className="h-4 w-4"
        />
        {t('ruleForm.enabledLabel')}
      </label>

      <label className="flex items-center gap-2 text-sm text-zinc-700">
        <input
          type="checkbox"
          checked={applyToExisting}
          onChange={(e) => setApplyToExisting(e.target.checked)}
          className="h-4 w-4"
        />
        {t('ruleForm.applyToExistingLabel')}
      </label>

      {submitError && (
        <div className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">{submitError}</div>
      )}

      <div className="flex flex-wrap items-center justify-end gap-2">
        <button
          type="button"
          onClick={onCancel}
          className="rounded-md border border-zinc-300 bg-white px-3 py-1.5 text-sm font-medium text-zinc-700 hover:bg-zinc-50"
        >
          {t('ruleForm.cancel')}
        </button>
        <button
          type="submit"
          disabled={saving || noFolders}
          className="rounded-md bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-60"
        >
          {saving ? t('common.saving') : (submitLabel ?? t('ruleForm.save'))}
        </button>
      </div>
    </form>
  );
}
