import { useCallback, useEffect, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';

import {
  listAccounts,
  createAccount,
  connectAccount,
  getAccount,
  deleteAccount,
  updateAccount,
  getApiOrigin,
} from '../../../api/endpoints/accounts';
import { syncEmailMetadata } from '../../../api/endpoints/emails';
import { syncDrafts } from '../../../api/endpoints/drafts';
import { toUiError } from '../../../api/client/errors';
import { getProviderMeta } from '../../../lib/providers';
import { useTranslation } from '../../../lib/i18n';
import type { AccountOut } from '../../../api/types/dto';
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
  reconnectAccount: (accountId: string) => Promise<void>;
  editAccountLabel: (accountId: string, label: string) => Promise<boolean>;
  error: UiError | null;
};

export default function useConnectedAccounts(mailboxId: string): UseConnectedAccountsReturn {
  const { t } = useTranslation();
  // Invalidated on add/remove/rename so the sidebar scope switcher
  // (useMailboxAccounts, keyed ['accounts', mailboxId] — kept literal here to
  // avoid a cross-feature import) refetches the updated account list.
  const queryClient = useQueryClient();
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

        setEntries(accounts.map((a) => ({ account: a, status: 'ready' as const })));
        setLoading(false);
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
      setError({ message: t('errors.popupBlocked') });
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
          message: outcome?.message ?? t('errors.connectFailed'),
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

    // Phase 2 — the account is connected: show its card as ready. The initial
    // history now downloads in the BACKGROUND (backfill), enqueued server-side
    // by the OAuth callback — the frontend no longer syncs metadata here for the
    // initial load. The live "Cargando… N correos" counter is server state
    // driven by useBackfillStatus (not by ``entry.status``), so the entry lands
    // directly in 'ready'. Drafts still sync best-effort.
    const account = connectedAccount;
    const accountId = account.account_id;
    setEntries((prev) => [...prev, { account, status: 'ready' }]);
    setSelectedProvider('');
    setDisplayLabel('');
    void queryClient.invalidateQueries({ queryKey: ['accounts', mailboxId] });
    // Kick the backfill-status poll: the job is already enqueued server-side, so
    // this refetch sees it pending/running and re-arms useBackfillStatus's
    // refetchInterval (idle otherwise — the counter would not appear until a
    // page remount).
    void queryClient.invalidateQueries({ queryKey: ['backfill-status', mailboxId] });
    void syncDrafts(mailboxId, accountId).catch(() => {});
  }, [canAdd, mailboxId, selectedProvider, displayLabel, t, queryClient]);

  const removeAccount = useCallback(
    async (accountId: string) => {
      setError(null);
      try {
        await deleteAccount(mailboxId, accountId);
        setEntries((prev) => prev.filter((e) => e.account.account_id !== accountId));
        void queryClient.invalidateQueries({ queryKey: ['accounts', mailboxId] });
      } catch (err) {
        setError(toUiError(err));
      }
    },
    [mailboxId, queryClient],
  );

  // Rename an already-connected account's label. The hook holds its accounts in
  // ``entries`` (useState, NOT TanStack Query — §1.2), so there is no query to
  // invalidate: the updated AccountOut from the backend replaces the entry's
  // ``account`` in place so the card re-renders with the new label.
  const editAccountLabel = useCallback(
    async (accountId: string, label: string): Promise<boolean> => {
      const trimmed = label.trim();
      if (trimmed.length === 0) return false;
      setError(null);
      try {
        const updated = await updateAccount(mailboxId, accountId, { display_label: trimmed });
        setEntries((prev) =>
          prev.map((e) => (e.account.account_id === accountId ? { ...e, account: updated } : e)),
        );
        void queryClient.invalidateQueries({ queryKey: ['accounts', mailboxId] });
        return true;
      } catch (err) {
        setError(toUiError(err));
        return false;
      }
    },
    [mailboxId, queryClient],
  );

  const reconnectAccount = useCallback(
    async (accountId: string) => {
      setError(null);

      // The popup must open synchronously inside the click gesture or the
      // browser blocks it; the authorization URL is assigned once known.
      const popup = window.open('about:blank', 'mailmanager-oauth-connect', OAUTH_POPUP_FEATURES);
      if (!popup) {
        setError({ message: t('errors.popupBlocked') });
        return;
      }

      // Show the syncing state on the card while the reconnect runs.
      setEntries((prev) =>
        prev.map((e) =>
          e.account.account_id === accountId ? { ...e, status: 'syncing' as const } : e,
        ),
      );

      // Re-run the interactive OAuth on the EXISTING account. Unlike addAccount
      // there is no createAccount and no rollback: a previously-connected
      // account is never deleted if the reconnect fails — the user retries.
      try {
        const start = await connectAccount(mailboxId, accountId);
        popup.location.href = start.authorization_url;

        const outcome = await waitForOAuthOutcome(popup, getApiOrigin());
        if (!popup.closed) popup.close();

        if (outcome?.ok === false) {
          // The provider/connect step explicitly failed. Keep the account.
          setError({
            message: outcome.message ?? t('errors.reconnectFailed'),
          });
          setEntries((prev) =>
            prev.map((e) =>
              e.account.account_id === accountId ? { ...e, status: 'ready' as const } : e,
            ),
          );
          return;
        }
        // outcome.ok === true (reported success) OR outcome === null (popup
        // closed/timed out without reporting). The email_address silent-close
        // proof used by addAccount does not apply here (the account already has
        // an email), so the re-sync below is what confirms the new token.
      } catch (err) {
        if (!popup.closed) popup.close();
        setError(toUiError(err));
        setEntries((prev) =>
          prev.map((e) =>
            e.account.account_id === accountId ? { ...e, status: 'ready' as const } : e,
          ),
        );
        return;
      }

      // Re-sync with the refreshed credentials. A still-dead token surfaces as
      // a sync failure here (status 'error'), prompting another retry.
      try {
        await Promise.all([
          syncEmailMetadata(mailboxId, accountId),
          syncDrafts(mailboxId, accountId).catch(() => {}),
        ]);
        setEntries((prev) =>
          prev.map((e) =>
            e.account.account_id === accountId ? { ...e, status: 'ready' as const } : e,
          ),
        );
      } catch (err) {
        setError(toUiError(err));
        setEntries((prev) =>
          prev.map((e) =>
            e.account.account_id === accountId ? { ...e, status: 'error' as const } : e,
          ),
        );
      }
    },
    [mailboxId, t],
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
    reconnectAccount,
    editAccountLabel,
    error,
  };
}
