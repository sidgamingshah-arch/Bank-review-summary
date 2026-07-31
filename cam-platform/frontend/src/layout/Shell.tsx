import { NavLink, Outlet, useNavigate } from 'react-router-dom';
import { useAuth } from '../auth/AuthContext';
import { useTheme } from '../theme';

interface NavItem {
  to: string;
  label: string;
  icon: string;
  roles: string[];
}

const NAV_ITEMS: NavItem[] = [
  { to: '/cases', label: 'Cases', icon: '📁', roles: ['analyst', 'reviewer'] },
  { to: '/admin/masters', label: 'Masters', icon: '⚙️', roles: ['business_admin'] },
  { to: '/admin/users', label: 'Users', icon: '👥', roles: ['it_admin'] },
  { to: '/audit', label: 'Audit', icon: '🛡️', roles: ['auditor', 'business_admin'] },
  {
    to: '/preferences',
    label: 'Preferences',
    icon: '🎚️',
    roles: ['business_admin', 'it_admin', 'analyst', 'reviewer', 'auditor'],
  },
];

export function Shell() {
  const { user, hasRole, logout } = useAuth();
  const navigate = useNavigate();
  const { theme, toggle } = useTheme();

  const items = NAV_ITEMS.filter((item) => hasRole(...item.roles));

  const doLogout = () => {
    logout();
    navigate('/login', { replace: true });
  };

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark">CAM</span> Studio
        </div>
        <div className="topbar-right">
          {user ? (
            <>
              <span className="topbar-user">{user.display_name}</span>
              {user.roles.map((r) => (
                <span key={r} className="chip role-badge">
                  {r.replace(/_/g, ' ')}
                </span>
              ))}
              <button
                type="button"
                className="theme-toggle"
                onClick={toggle}
                title={theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme'}
                aria-label="Toggle color theme"
              >
                {theme === 'dark' ? '☀️' : '🌙'}
              </button>
              <button type="button" className="btn btn-sm" onClick={doLogout}>
                Log out
              </button>
            </>
          ) : null}
        </div>
      </header>
      <div className="app-body">
        <nav className="sidenav">
          {items.map((item) => (
            <NavLink key={item.to} to={item.to} className={({ isActive }) => (isActive ? 'nav-link active' : 'nav-link')}>
              <span className="nav-ico" aria-hidden="true">
                {item.icon}
              </span>
              {item.label}
            </NavLink>
          ))}
        </nav>
        <main className="main-area">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
