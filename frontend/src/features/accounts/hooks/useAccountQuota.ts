import { useQuery } from '@tanstack/react-query';

import { getAccountQuota } from '../../../api/endpoints/accounts';

type UseAccountQuotaReturn = {
  connected: number | undefined;
  limit: number | undefined;
  remaining: number | undefined;
  atLimit: boolean;
};

/**
 * Per-user connected-account quota (``GET /accounts/quota``). A separate
 * TanStack query from ``useConnectedAccounts`` (which holds its accounts in
 * useState — see frontend_guide §1.2), keyed ``['accounts-quota']`` so add /
 * remove flows invalidate it. Degrades gracefully: while the query is loading
 * or has failed ``data`` is undefined, so ``atLimit`` stays false (the button is
 * never wrongly blocked) and the counter simply does not render — the 409
 * ``account_limit_exceeded`` translated by ``toUiError`` is the real guard.
 */
export default function useAccountQuota(): UseAccountQuotaReturn {
  const { data } = useQuery({
    queryKey: ['accounts-quota'],
    queryFn: ({ signal }) => getAccountQuota(signal),
  });

  return {
    connected: data?.connected,
    limit: data?.limit,
    remaining: data ? data.limit - data.connected : undefined,
    atLimit: data ? data.connected >= data.limit : false,
  };
}
