import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import { api, clearToken, getToken, setToken } from '../api/client';
import type { TokenResponse, User } from '../api/types';

interface AuthState {
  user: User | null;
  loading: boolean;
  login: (username: string, password: string) => Promise<User>;
  logout: () => void;
  hasRole: (...roles: string[]) => boolean;
}

const AuthContext = createContext<AuthState | null>(null);

/** Preferred landing route per role, in priority order. */
export function homeForRoles(roles: string[]): string {
  if (roles.includes('analyst') || roles.includes('reviewer')) return '/cases';
  if (roles.includes('business_admin')) return '/admin/masters';
  if (roles.includes('it_admin')) return '/admin/users';
  if (roles.includes('auditor')) return '/audit';
  return '/preferences';
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState<boolean>(() => Boolean(getToken()));

  useEffect(() => {
    if (!getToken()) return;
    let cancelled = false;
    api
      .get<User>('/api/auth/me')
      .then((me) => {
        if (!cancelled) setUser(me);
      })
      .catch(() => {
        if (!cancelled) {
          clearToken();
          setUser(null);
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const login = useCallback(async (username: string, password: string): Promise<User> => {
    const res = await api.post<TokenResponse>('/api/auth/token', { username, password });
    setToken(res.access_token);
    setUser(res.user);
    return res.user;
  }, []);

  const logout = useCallback(() => {
    clearToken();
    setUser(null);
  }, []);

  const hasRole = useCallback(
    (...roles: string[]) => Boolean(user && roles.some((r) => user.roles.includes(r))),
    [user],
  );

  const value = useMemo<AuthState>(
    () => ({ user, loading, login, logout, hasRole }),
    [user, loading, login, logout, hasRole],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within AuthProvider');
  return ctx;
}
