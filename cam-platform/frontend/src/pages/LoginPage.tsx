import { useState } from 'react';
import type { FormEvent } from 'react';
import { Navigate, useNavigate } from 'react-router-dom';
import { homeForRoles, useAuth } from '../auth/AuthContext';
import { errorMessage } from '../api/client';

const DEMO_USERS = [
  { username: 'admin1', role: 'business admin' },
  { username: 'admin2', role: 'business admin' },
  { username: 'itadmin', role: 'IT admin' },
  { username: 'analyst1', role: 'analyst' },
  { username: 'reviewer1', role: 'reviewer' },
  { username: 'auditor1', role: 'auditor' },
];

export function LoginPage() {
  const { user, login } = useAuth();
  const navigate = useNavigate();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (user) return <Navigate to={homeForRoles(user.roles)} replace />;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const me = await login(username.trim(), password);
      navigate(homeForRoles(me.roles), { replace: true });
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login-screen">
      <div className="login-card">
        <div className="login-brand">
          <span className="brand-mark">CAM</span> Studio
        </div>
        <p className="login-sub">AI-assisted Credit Assessment Memo platform</p>
        <form onSubmit={submit}>
          <div className="field">
            <label htmlFor="login-username">Username</label>
            <input
              id="login-username"
              className="input"
              value={username}
              autoComplete="username"
              autoFocus
              onChange={(e) => setUsername(e.target.value)}
            />
          </div>
          <div className="field">
            <label htmlFor="login-password">Password</label>
            <input
              id="login-password"
              className="input"
              type="password"
              value={password}
              autoComplete="current-password"
              onChange={(e) => setPassword(e.target.value)}
            />
          </div>
          {error ? <div className="banner banner-error">{error}</div> : null}
          <button type="submit" className="btn btn-primary btn-block" disabled={busy || !username || !password}>
            {busy ? 'Signing in…' : 'Sign in'}
          </button>
        </form>
        <div className="login-hint">
          <div className="login-hint-title">Demo users (password: Demo#2026)</div>
          <ul>
            {DEMO_USERS.map((u) => (
              <li key={u.username}>
                <button type="button" className="link-btn" onClick={() => setUsername(u.username)}>
                  {u.username}
                </button>{' '}
                — {u.role}
              </li>
            ))}
          </ul>
        </div>
      </div>
    </div>
  );
}
