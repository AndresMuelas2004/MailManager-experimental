import { useCallback, useEffect, useMemo, useState } from 'react';
import type { ReactNode } from 'react';

import {
  loginWithGoogle,
  loginWithMicrosoft as apiLoginWithMicrosoft,
  devLogin as apiDevLogin,
  getMe,
  logout as apiLogout,
  deleteMe,
} from '../../api/endpoints/auth';
import { toUiError } from '../../api/client/errors';
import type { UserOut } from '../../api/types/dto';
import type { UiError } from '../../api/client/errors';
import { AuthContext } from './AuthContext';

type Props = { children: ReactNode };

export default function AuthProvider({ children }: Props) {
  const [user, setUser] = useState<UserOut | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<UiError | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function bootstrapAuth() {
      try {
        const current = await getMe();
        if (!cancelled) setUser(current);
        return;
      } catch {
        // Not authenticated. In local development only, optionally skip the
        // login screen by minting a session through the dev-login backdoor
        // (opt-in via VITE_DEV_AUTO_LOGIN). Any failure — endpoint disabled
        // in the deploy, dev user missing — silently falls back to the
        // normal unauthenticated state and the login surface renders.
        if (import.meta.env.DEV && import.meta.env.VITE_DEV_AUTO_LOGIN === 'true') {
          try {
            const response = await apiDevLogin();
            if (!cancelled) setUser(response.user);
            return;
          } catch {
            // dev-login unavailable → fall through to unauthenticated
          }
        }
        if (!cancelled) setUser(null);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    void bootstrapAuth();
    return () => {
      cancelled = true;
    };
  }, []);

  const login = useCallback(async (idToken: string) => {
    setError(null);
    try {
      const response = await loginWithGoogle(idToken);
      setUser(response.user);
    } catch (err) {
      setError(toUiError(err));
      throw err;
    }
  }, []);

  const loginWithMicrosoft = useCallback(async (idToken: string) => {
    setError(null);
    try {
      const response = await apiLoginWithMicrosoft(idToken);
      setUser(response.user);
    } catch (err) {
      setError(toUiError(err));
      throw err;
    }
  }, []);

  const devLogin = useCallback(async () => {
    setError(null);
    try {
      const response = await apiDevLogin();
      setUser(response.user);
    } catch (err) {
      setError(toUiError(err));
      throw err;
    }
  }, []);

  const logout = useCallback(async () => {
    setError(null);
    try {
      await apiLogout();
      setUser(null);
    } catch (err) {
      setError(toUiError(err));
    }
  }, []);

  const deleteCurrentUser = useCallback(async (): Promise<boolean> => {
    setError(null);
    try {
      await deleteMe();
      return true;
    } catch (err) {
      setError(toUiError(err));
      return false;
    }
  }, []);

  const value = useMemo(
    () => ({
      user,
      loading,
      error,
      login,
      loginWithMicrosoft,
      devLogin,
      logout,
      deleteCurrentUser,
    }),
    [user, loading, error, login, loginWithMicrosoft, devLogin, logout, deleteCurrentUser],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
