import { useState } from 'react';

import RichTextEditor from '../../../components/ui/RichTextEditor';
import Spinner from '../../../components/common/Spinner';
import { normalizeEmpty } from '../../../lib/richText';
import { useTranslation } from '../../../lib/i18n';
import type { AccountOut } from '../../../api/types/dto';

const SIGNATURE_MAX_CHARS = 10_000;

type Props = {
  account: AccountOut;
  saving: boolean;
  // True right after a successful save of THIS row (owned by the page so it
  // survives the key-remount that re-seeds the editor with the sanitised
  // value). Hidden again as soon as the user edits.
  saved: boolean;
  // Receives the normalised HTML ('' clears the signature). The parent wires
  // this to the hook's ``updateSignature`` (the hook owns the fetch + error
  // translation); the row only collects the draft and emits it.
  onSave: (signatureHtml: string) => void;
  // Notifies the parent the draft changed so it can clear the "saved" flag.
  onDirty: () => void;
};

export default function AccountSignatureRow({ account, saving, saved, onSave, onDirty }: Props) {
  const { t } = useTranslation();
  // Seeded once from the persisted value. The page remounts this row (via a
  // ``key`` that includes the persisted signature) when the backend returns a
  // sanitised value, so this initial seed always reflects the latest stored
  // HTML without a setState-in-effect re-seed.
  const [html, setHtml] = useState(account.signature_html ?? '');

  const tooLong = html.length > SIGNATURE_MAX_CHARS;
  const canSave = !saving && !tooLong;

  const handleChange = (next: string) => {
    setHtml(next);
    if (saved) onDirty();
  };

  const handleSave = () => {
    if (!canSave) return;
    onSave(normalizeEmpty(html));
  };

  const label = account.email_address ?? account.display_label;

  return (
    <li className="flex flex-col gap-3 border-b border-zinc-100 px-4 py-5 lg:px-8">
      <div className="flex flex-col">
        <span className="text-sm font-semibold text-zinc-900">{account.display_label}</span>
        {account.email_address && (
          <span className="text-[13px] text-zinc-500">{account.email_address}</span>
        )}
      </div>

      <RichTextEditor
        value={html}
        onChange={handleChange}
        placeholder={t('settings.signature.placeholder')}
        ariaLabel={`${t('settings.signature.editorAria')} — ${label}`}
        disabled={saving}
      />

      <div className="flex items-center gap-3">
        <button
          type="button"
          onClick={handleSave}
          disabled={!canSave}
          className="inline-flex h-9 items-center gap-2 rounded-lg bg-blue-600 px-4 text-sm font-semibold text-white transition-colors hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {saving && <Spinner size="sm" color="white" />}
          {saving ? t('settings.signature.saving') : t('settings.signature.save')}
        </button>
        {tooLong && (
          <span className="text-[13px] text-red-600">{t('settings.signature.tooLong')}</span>
        )}
        {!saving && !tooLong && saved && (
          <span className="text-[13px] text-green-600">{t('settings.signature.saved')}</span>
        )}
      </div>
    </li>
  );
}
