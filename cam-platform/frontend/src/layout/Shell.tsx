import { NavLink, Outlet, useNavigate } from 'react-router-dom';
import { useAuth } from '../auth/AuthContext';

interface NavItem {
  to: string;
  label: string;
  roles: string[];
}

const NAV_ITEMS: NavItem[] = [
  { to: '/cases', label: 'Cases', roles: ['analyst', 'reviewer'] },
  { to: '/admin/masters', label: 'Masters', roles: ['business_admin'] },
  { to: '/admin/users', label: 'Users', roles: ['it_admin'] },
  { to: '/audit', label: 'Audit', roles: ['auditor', 'business_admin'] },
  { to: '/preferences', label: 'Preferences', roles: ['business_admin', 'it_admin', 'analyst', 'reviewer', 'auditor'] },
];

export function Shell() {
  const { user, hasRole, logout } = useAuth();
  const navigate = useNavigate();

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
                <span key={r} className="chip chip-navy role-badge">
                  {r.replace(/_/g, ' ')}
                </span>
              ))}
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
