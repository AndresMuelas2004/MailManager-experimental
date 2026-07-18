import { useState } from 'react';
import { Check } from 'lucide-react';

import { useTranslation } from '../../../lib/i18n';
import type { FolderCreate, FolderOut } from '../../../api/types/dto';

type Props = {
  initial?: FolderOut;
  saving: boolean;
  submitLabel?: string;
  onSubmit: (payload: FolderCreate) => Promise<void>;
  onCancel: () => void;
};

// Preset palette. A folder colour is a MISSELA-side hint (see the general
// description): shown in the app and in Gmail; Outlook may render the category
// without colour. ``null``/absent means "no colour".
const COLOR_PRESETS = [
  '#ef4444',
  '#f97316',
  '#eab308',
  '#22c55e',
  '#14b8a6',
  '#3b82f6',
  '#6366f1',
  '#a855f7',
  '#ec4899',
  '#78716c',
] as const;

export default function FolderForm({ initial, saving, submitLabel, onSubmit, onCancel }: Props) {
  const { t } = useTranslation();
  const [name, setName] = useState(initial?.name ?? '');
  const [color, setColor] = useState<string | null>(initial?.color ?? null);
  const [submitError, setSubmitError] = useState<string | null>(null);

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    setSubmitError(null);
    const trimmedName = name.trim();
    if (trimmedName.length === 0) {
      setSubmitError(t('folderForm.errorNameRequired'));
      return;
    }
    const payload: FolderCreate = { name: trimmedName };
    if (color) payload.color = color;
    try {
      await onSubmit(payload);
    } catch (err) {
      const message = err instanceof Error ? err.message : t('folderForm.errorSaveFailed');
      setSubmitError(message);
    }
  };

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-6">
      <div className="flex flex-col gap-2">
        <label htmlFor="folder-name" className="text-[13px] font-semibold text-zinc-700">
          {t('folderForm.nameLabel')}
        </label>
        <input
          id="folder-name"
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          maxLength={120}
          required
          placeholder={t('folderForm.namePlaceholder')}
          className="rounded-md border border-zinc-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none"
        />
      </div>

      <fieldset className="flex flex-col gap-2">
        <legend className="text-[13px] font-semibold text-zinc-700">
          {t('folderForm.colorLabel')}
        </legend>
        <p className="text-xs text-zinc-500">{t('folderForm.colorHelp')}</p>
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={() => setColor(null)}
            aria-label={t('folderForm.colorNone')}
            aria-pressed={color === null}
            className={`grid h-8 w-8 place-items-center rounded-full border ${
              color === null ? 'border-blue-600 ring-2 ring-blue-200' : 'border-zinc-300'
            } bg-white text-zinc-400`}
          >
            {color === null ? <Check className="h-4 w-4 text-blue-600" /> : '—'}
          </button>
          {COLOR_PRESETS.map((preset) => (
            <button
              key={preset}
              type="button"
              onClick={() => setColor(preset)}
              aria-label={preset}
              aria-pressed={color === preset}
              // The swatch colour is genuinely dynamic (user data), so an inline
              // style is the correct escape hatch — Tailwind cannot express it.
              style={{ backgroundColor: preset }}
              className={`grid h-8 w-8 place-items-center rounded-full ${
                color === preset ? 'ring-2 ring-offset-2 ring-zinc-800' : ''
              }`}
            >
              {color === preset ? <Check className="h-4 w-4 text-white" /> : null}
            </button>
          ))}
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
          {t('folderForm.cancel')}
        </button>
        <button
          type="submit"
          disabled={saving}
          className="rounded-md bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-60"
        >
          {saving ? t('common.saving') : (submitLabel ?? t('folderForm.save'))}
        </button>
      </div>
    </form>
  );
}
