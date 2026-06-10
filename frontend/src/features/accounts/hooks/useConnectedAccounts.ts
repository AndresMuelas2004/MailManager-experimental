import { useCallback, useEffect, useState } from 'react';

import {
  listAccounts,
  createAccount,
  connectAccount,
  getAccount,
  deleteAccount,
  getApiOrigin,
} from '../../../api/endpoints/accounts';
import { syncEmailMetadata, listEmails } from '../../../api/endpoints/emails';
import { syncDrafts } from '../../../api/endpoints/drafts';
import { toUiError } from '../../../api/client/errors';
import { getProviderMeta } from '../../../lib/providers';
import type { AccountOut, EmailMetadataOut } from '../../../api/types/dto';
import type { UiError } from '../../../api/client/errors';

const OAUTH_RESULT_SOURCE = 'mailmanager-oauth';
const OAUTH_POPUP_FEATURES = 'popup,width=560,height=720';
const OAUTH_POPUP_POLL_MS = 500;
const OAUTH_TIMEOUT_MS = 5 * 60_000;

type OAuthOutcome = { ok: boolean; message?: string };

/**
 * Resolve the connect result reported by the OAuth callback popup.
 *
 * Resolves with the postMessage payload sent by the callback page, or with
 * `null` when the popup closes (or times out) without reporting — the
 * caller then confirms against the backend.
 */
function waitForOAuthOutcome(popup: Window, apiOrigin: string): Promise<OAuthOutcome | null> {
  return new Promise((resolve) => {
    let settled = false;
    const settle = (value: OAuthOutcome | null) => {
      if (settled) return;
      settled = true;
      window.removeEventListener('message', onMessage);
      window.clearInterval(closedPoll);
      window.clearTimeout(timeout);
      resolve(value);
    };
    const onMessage = (event: MessageEvent) => {
      if (event.origin !== apiOrigin) return;
      const data = event.data as { source?: string; ok?: boolean; message?: string } | null;
      if (!data || data.source !== OAUTH_RESULT_SOURCE) return;
      settle({
        ok: data.ok === true,
        message: typeof data.message === 'string' ? data.message : undefined,
      });
    };
    window.addEventListener('message', onMessage);
    const closedPoll = window.setInterval(() => {
      if (popup.closed) settle(null);
    }, OAUTH_POPUP_POLL_MS);
    const timeout = window.setTimeout(() => settle(null), OAUTH_TIMEOUT_MS);
  });
}

export type AccountEntry = {
  account: AccountOut;
  emails: EmailMetadataOut[];
  status: 'syncing' | 'ready' | 'error';
};

type UseConnectedAccountsReturn = {
  entries: AccountEntry[];
  loading: boolean;
  displayLabel: string;
  setDisplayLabel: (v: string) => void;
  selectedProvider: string;
  setSelectedProvider: (v: string) => void;
  canAdd: boolean;
  addingAccount: boolean;
  addAccount: () => Promise<void>;
  removeAccount: (accountId: string) => Promise<void>;
  error: UiError | null;
};

export default function useConnectedAccounts(mailboxId: string): UseConnectedAccountsReturn {
  const [entries, setEntries] = useState<AccountEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [displayLabel, setDisplayLabel] = useState('');
  const [selectedProvider, setSelectedProvider] = useState('');
  const [addingAccount, setAddingAccount] = useState(false);
  const [error, setError] = useState<UiError | null>(null);

  const canAdd = selectedProvider.length > 0 && !addingAccount;

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const accounts = await listAccounts(mailboxId);
        if (cancelled) return;

        const initialEntries: AccountEntry[] = accounts.map((a) => ({
          account: a,
          emails: [],
          status: 'syncing' as const,
        }));
        setEntries(initialEntries);
        setLoading(false);

        await Promise.all(
          accounts.map(async (account) => {
            try {
              const { items } = await listEmails(mailboxId, 'ALL_MAIL', account.account_id);
              if (cancelled) return;
              setEntries((prev) =>
                prev.map((e) =>
                  e.account.account_id === account.account_id
                    ? { ...e, emails: items.slice(0, 3), status: 'ready' as const }
                    : e,
                ),
              );
            } catch {
              if (cancelled) return;
              setEntries((prev) =>
                prev.map((e) =>
                  e.account.account_id === account.account_id
                    ? { ...e, status: 'ready' as const }
                    : e,
                ),
              );
            }
          }),
        );
      } catch (err) {
        if (!cancelled) {
          setError(toUiError(err));
          setLoading(false);
        }
      }
    }

    load();
    return () => {
      cancelled = true;
    };
  }, [mailboxId]);

  const addAccount = useCallback(async () => {
    if (!canAdd) return;
    setError(null);

    // The popup must open synchronously inside the click gesture or the
    // browser blocks it; the authorization URL is assigned once known.
    const popup = window.open('about:blank', 'mailmanager-oauth-connect', OAUTH_POPUP_FEATURES);
    if (!popup) {
      setError({
        message:
          'El navegador ha bloqueado la ventana de autenticación. Permite ventanas emergentes para este sitio e inténtalo de nuevo.',
      });
      return;
    }
    setAddingAccount(true);

    const providerLabel = getProviderMeta(selectedProvider).label;
    const label = displayLabel.trim() || providerLabel;

    // Phase 1 — create + interactive OAuth. No card is shown yet; if
    // anything fails the created account row is rolled back so no empty
    // account card is ever left behind.
    let connectedAccount: AccountOut | null = null;
    let createdAccountId: string | null = null;
    try {
      const account = await createAccount(mailboxId, {
        provider: selectedProvider,
        display_label: label,
      });
      createdAccountId = account.account_id;

      const start = await connectAccount(mailboxId, account.account_id);
      popup.location.href = start.authorization_url;

      const outcome = await waitForOAuthOutcome(popup, getApiOrigin());
      if (!popup.closed) popup.close();

      if (outcome?.ok === true) {
        connectedAccount = await getAccount(mailboxId, account.account_id);
      } else if (outcome === null) {
        // Popup closed (or timed out) without reporting: the callback may
        // still have landed — a persisted email_address is the proof.
        const fresh = await getAccount(mailboxId, account.account_id).catch(() => null);
        if (fresh?.email_address) connectedAccount = fresh;
      }

      if (!connectedAccount) {
        await deleteAccount(mailboxId, account.account_id).catch(() => {});
        createdAccountId = null;
        setError({
          message:
            outcome?.message ??
            'No se completó la autenticación de la cuenta, así que no se ha añadido. Inténtalo de nuevo.',
        });
        return;
      }
    } catch (err) {
      if (!popup.closed) popup.close();
      if (createdAccountId) {
        await deleteAccount(mailboxId, createdAccountId).catch(() => {});
      }
      setError(toUiError(err));
      return;
    } finally {
      setAddingAccount(false);
    }

    // Phase 2 — the account is connected: show its card and sync. Failures
    // here never roll the account back.
    const account = connectedAccount;
    const accountId = account.account_id;
    setEntries((prev) => [...prev, { account, emails: [], status: 'syncing' }]);
    setSelectedProvider('');
    setDisplayLabel('');

    try {
      const [syncResult] = await Promise.all([
        syncEmailMetadata(mailboxId, accountId),
        syncDrafts(mailboxId, accountId).catch(() => {}),
      ]);

      if (syncResult.total_synced > 0) {
        const { items } = await listEmails(mailboxId, 'ALL_MAIL', accountId);
        setEntries((prev) =>
          prev.map((e) =>
            e.account.account_id === accountId
              ? { ...e, emails: items.slice(0, 3), status: 'ready' }
              : e,
          ),
        );
      } else {
        setEntries((prev) =>
          prev.map((e) => (e.account.account_id === accountId ? { ...e, status: 'ready' } : e)),
        );
      }
    } catch (err) {
      setError(toUiError(err));
      setEntries((prev) =>
        prev.map((e) =>
          e.account.account_id === accountId ? { ...e, status: 'error' as const } : e,
        ),
      );
    }
  }, [canAdd, mailboxId, selectedProvider, displayLabel]);

  const removeAccount = useCallback(
    async (accountId: string) => {
      setError(null);
      try {
        await deleteAccount(mailboxId, accountId);
        setEntries((prev) => prev.filter((e) => e.account.account_id !== accountId));
      } catch (err) {
        setError(toUiError(err));
      }
    },
    [mailboxId],
  );

  return {
    entries,
    loading,
    displayLabel,
    setDisplayLabel,
    selectedProvider,
    setSelectedProvider,
    canAdd,
    addingAccount,
    addAccount,
    removeAccount,
    error,
  };
}
