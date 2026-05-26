import { useMemo, useState } from 'react';

import type {
  AccountOut,
  MailboxOut,
  VirtualMailboxCreate,
  VirtualMailboxFilterBox,
  VirtualMailboxFilterPayload,
  VirtualMailboxOut,
  VirtualMailboxScopeKind,
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
  from_domain: string;
  subject_contains: string;
  is_read: '' | 'true' | 'false';
  is_favorite: '' | 'true' | 'false';
};

const EMPTY_FILTER: FilterDraft = {
  box: '',
  from_email: '',
  from_domain: '',
  subject_contains: '',
  is_read: '',
  is_favorite: '',
};

const SCOPE_OPTIONS: Array<{ value: VirtualMailboxScopeKind; label: string }> = [
  { value: 'all', label: 'Todas las bandejas del usuario' },
  { value: 'mailbox', label: 'Una bandeja concreta' },
  { value: 'accounts', label: 'Una o varias cuentas seleccionadas' },
];

const BOX_OPTIONS: Array<{ value: VirtualMailboxFilterBox; label: string }> = [
  { value: 'ALL_MAIL', label: 'Bandeja unificada (sin trash/spam)' },
  { value: 'SENT', label: 'Enviados' },
  { value: 'SPAM', label: 'Spam' },
  { value: 'TRASH', label: 'Papelera' },
];

function pickFilter(filter: FilterDraft): VirtualMailboxFilterPayload {
  const out: VirtualMailboxFilterPayload = {};
  if (filter.box) out.box = filter.box;
  if (filter.from_email.trim()) out.from_email = filter.from_email.trim();
  if (filter.from_domain.trim()) out.from_domain = filter.from_domain.trim();
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
  if (typeof raw.from_domain === 'string') result.from_domain = raw.from_domain;
  if (typeof raw.subject_contains === 'string') result.subject_contains = raw.subject_contains;
  if (typeof raw.is_read === 'boolean') result.is_read = raw.is_read ? 'true' : 'false';
  if (typeof raw.is_favorite === 'boolean') result.is_favorite = raw.is_favorite ? 'true' : 'false';
  return result;
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
  const [name, setName] = useState(initial?.display_name ?? '');
  const [scopeKind, setScopeKind] = useState<VirtualMailboxScopeKind>(initial?.scope_kind ?? 'all');
  const [scopeMailboxId, setScopeMailboxId] = useState<string>(() => {
    const raw = initial?.scope_payload as Record<string, unknown> | undefined;
    return typeof raw?.mailbox_id === 'string' ? raw.mailbox_id : '';
  });
  const [scopeAccountIds, setScopeAccountIds] = useState<string[]>(() => {
    const raw = initial?.scope_payload as Record<string, unknown> | undefined;
    return Array.isArray(raw?.account_ids) ? (raw.account_ids as unknown[]).map(String) : [];
  });
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

  const sortedMailboxes = useMemo(
    () => [...mailboxes].sort((a, b) => (a.display_name ?? '').localeCompare(b.display_name ?? '')),
    [mailboxes],
  );

  const handleAccountToggle = (accountId: string) => {
    setScopeAccountIds((prev) =>
      prev.includes(accountId) ? prev.filter((id) => id !== accountId) : [...prev, accountId],
    );
  };

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    setSubmitError(null);
    const trimmedName = name.trim();
    if (trimmedName.length === 0) {
      setSubmitError('Indica un nombre para la bandeja ficticia.');
      return;
    }
    if (scopeKind === 'mailbox' && !scopeMailboxId) {
      setSubmitError('Selecciona una bandeja real para el alcance.');
      return;
    }
    if (scopeKind === 'accounts' && scopeAccountIds.length === 0) {
      setSubmitError('Selecciona al menos una cuenta para el alcance.');
      return;
    }
    const payload: VirtualMailboxCreate = {
      display_name: trimmedName,
      scope_kind: scopeKind,
      scope_payload:
        scopeKind === 'mailbox'
          ? { mailbox_id: scopeMailboxId }
          : scopeKind === 'accounts'
            ? { account_ids: scopeAccountIds }
            : {},
      filter_payload: pickFilter(filter),
    };
    try {
      await onSubmit(payload);
    } catch (err) {
      const message =
        err instanceof Error ? err.message : 'No se pudo guardar la bandeja ficticia.';
      setSubmitError(message);
    }
  };

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-6">
      <div className="flex flex-col gap-2">
        <label htmlFor="vmb-name" className="text-[13px] font-semibold text-zinc-700">
          Nombre
        </label>
        <input
          id="vmb-name"
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          maxLength={120}
          required
          placeholder="Ej. Facturación · Newsletters · Notificaciones de banca"
          className="rounded-md border border-zinc-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none"
        />
      </div>

      <fieldset className="flex flex-col gap-2">
        <legend className="text-[13px] font-semibold text-zinc-700">Alcance</legend>
        <div className="flex flex-col gap-2">
          {SCOPE_OPTIONS.map((option) => (
            <label key={option.value} className="flex items-center gap-2 text-sm text-zinc-700">
              <input
                type="radio"
                name="vmb-scope"
                value={option.value}
                checked={scopeKind === option.value}
                onChange={() => setScopeKind(option.value)}
              />
              {option.label}
            </label>
          ))}
        </div>

        {scopeKind === 'mailbox' && (
          <select
            value={scopeMailboxId}
            onChange={(e) => setScopeMailboxId(e.target.value)}
            className="mt-1 rounded-md border border-zinc-300 px-3 py-2 text-sm"
          >
            <option value="" disabled>
              Selecciona una bandeja…
            </option>
            {sortedMailboxes.map((m) => (
              <option key={m.mailbox_id} value={m.mailbox_id}>
                {m.display_name ?? m.mailbox_id}
              </option>
            ))}
          </select>
        )}

        {scopeKind === 'accounts' && (
          <div className="mt-1 max-h-56 overflow-auto rounded-md border border-zinc-300 p-2 text-sm">
            {sortedMailboxes.length === 0 ? (
              <div className="px-2 py-3 text-xs text-zinc-500">
                No tienes ninguna cuenta conectada todavía.
              </div>
            ) : (
              sortedMailboxes.map((mailbox) => {
                const accs = accountsByMailbox.get(mailbox.mailbox_id) ?? [];
                if (accs.length === 0) return null;
                return (
                  <div key={mailbox.mailbox_id} className="mb-2 last:mb-0">
                    <div className="px-1 text-[11px] font-semibold uppercase tracking-wider text-zinc-500">
                      {mailbox.display_name ?? mailbox.mailbox_id}
                    </div>
                    {accs.map((account) => {
                      const checked = scopeAccountIds.includes(account.account_id);
                      return (
                        <label
                          key={account.account_id}
                          className="flex items-center gap-2 px-1 py-1 text-sm text-zinc-700"
                        >
                          <input
                            type="checkbox"
                            checked={checked}
                            onChange={() => handleAccountToggle(account.account_id)}
                          />
                          <span className="truncate">
                            {account.email_address ?? account.display_label}{' '}
                            <span className="text-xs text-zinc-400">({account.provider})</span>
                          </span>
                        </label>
                      );
                    })}
                  </div>
                );
              })
            )}
          </div>
        )}
      </fieldset>

      <fieldset className="flex flex-col gap-3">
        <legend className="text-[13px] font-semibold text-zinc-700">Filtros</legend>
        <p className="text-xs text-zinc-500">
          Si no rellenas ninguno, la bandeja muestra todo el alcance seleccionado (excluyendo
          papelera y spam salvo que indiques lo contrario).
        </p>

        <label className="flex flex-col gap-1 text-sm">
          <span className="text-xs font-medium text-zinc-600">Carpeta</span>
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
            <option value="">Cualquiera (excluye papelera/spam)</option>
            {BOX_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
        </label>

        <label className="flex flex-col gap-1 text-sm">
          <span className="text-xs font-medium text-zinc-600">Remitente exacto</span>
          <input
            type="text"
            value={filter.from_email}
            onChange={(e) => setFilter((prev) => ({ ...prev, from_email: e.target.value }))}
            placeholder="alguien@empresa.com"
            className="rounded-md border border-zinc-300 px-2 py-1.5 text-sm"
          />
        </label>

        <label className="flex flex-col gap-1 text-sm">
          <span className="text-xs font-medium text-zinc-600">Dominio del remitente</span>
          <input
            type="text"
            value={filter.from_domain}
            onChange={(e) => setFilter((prev) => ({ ...prev, from_domain: e.target.value }))}
            placeholder="empresa.com"
            className="rounded-md border border-zinc-300 px-2 py-1.5 text-sm"
          />
        </label>

        <label className="flex flex-col gap-1 text-sm">
          <span className="text-xs font-medium text-zinc-600">Asunto contiene</span>
          <input
            type="text"
            value={filter.subject_contains}
            onChange={(e) => setFilter((prev) => ({ ...prev, subject_contains: e.target.value }))}
            placeholder="factura, pedido…"
            className="rounded-md border border-zinc-300 px-2 py-1.5 text-sm"
          />
        </label>

        <div className="flex gap-3">
          <label className="flex flex-1 flex-col gap-1 text-sm">
            <span className="text-xs font-medium text-zinc-600">Leído / no leído</span>
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
              <option value="">Cualquiera</option>
              <option value="false">Solo no leídos</option>
              <option value="true">Solo leídos</option>
            </select>
          </label>

          <label className="flex flex-1 flex-col gap-1 text-sm">
            <span className="text-xs font-medium text-zinc-600">Favoritos</span>
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
              <option value="">Cualquiera</option>
              <option value="true">Solo favoritos</option>
              <option value="false">Excluir favoritos</option>
            </select>
          </label>
        </div>
      </fieldset>

      {submitError && (
        <div className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">{submitError}</div>
      )}

      <div className="flex items-center justify-end gap-2">
        <button
          type="button"
          onClick={onCancel}
          className="rounded-md border border-zinc-300 bg-white px-3 py-1.5 text-sm font-medium text-zinc-700 hover:bg-zinc-50"
        >
          Cancelar
        </button>
        <button
          type="submit"
          disabled={saving}
          className="rounded-md bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-60"
        >
          {saving ? 'Guardando…' : (submitLabel ?? 'Guardar')}
        </button>
      </div>
    </form>
  );
}
