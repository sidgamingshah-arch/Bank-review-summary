import type { ReactNode } from 'react';
import { Navigate } from 'react-router-dom';
import { homeForRoles, useAuth } from './AuthContext';
import { PageLoading } from '../components/Spinner';
import { EmptyState } from '../components/EmptyState';

export function RequireAuth({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth();
  if (loading) return <PageLoading label="Signing you in…" />;
  if (!user) return <Navigate to="/login" replace />;
  return <>{children}</>;
}

export function RequireRole({ roles, children }: { roles: string[]; children: ReactNode }) {
  const { hasRole } = useAuth();
  if (!hasRole(...roles)) {
    return (
      <div className="page">
        <EmptyState title="Not authorised" hint={`This area requires one of: ${roles.join(', ')}.`} />
      </div>
    );
  }
  return <>{children}</>;
}

export function HomeRedirect() {
  const { user } = useAuth();
  if (!user) return <Navigate to="/login" replace />;
  return <Navigate to={homeForRoles(user.roles)} replace />;
}
