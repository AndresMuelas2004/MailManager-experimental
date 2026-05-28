import { useCallback, useState } from 'react';

import { useAuth } from '../../../app/providers/AuthContext';
import { toUiError } from '../../../api/client/errors';
import type { UiError } from '../../../api/client/errors';

type UseDevLoginReturn = {
  trigger: () => Promise<void>;
  error: UiError | null;
  loading: boolean;
};

export default function useDevLogin(): UseDevLoginReturn {
  const { devLogin } = useAuth();
  const [error, setError] = useState<UiError | null>(null);
  const [loading, setLoading] = useState(false);

  const trigger = useCallback(async () => {
    setError(null);
    setLoading(true);
    try {
      await devLogin();
    } catch (err) {
      setError(toUiError(err));
    } finally {
      setLoading(false);
    }
  }, [devLogin]);

  return { trigger, error, loading };
}
