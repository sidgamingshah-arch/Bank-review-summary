import { NavLink, Outlet, useNavigate } from 'react-router-dom';
import type { LucideIcon } from 'lucide-react';
import {
  FolderOpen,
  Settings2,
  ShieldCheck,
  Users,
  SlidersHorizontal,
  Sun,
  Moon,
  LogOut,
  Search,
} from 'lucide-react';
import { useAuth } from '../auth/AuthContext';
import { useTheme } from '../theme';
import { NotificationBell } from './NotificationBell';

interface NavItem {
  to: string;
  label: string;
  icon: LucideIcon;
  roles: string[];
}

interface NavGroup {
  label: string;
  items: NavItem[];
}

const ALL_ROLES = ['business_admin', 'it_admin', 'analyst', 'reviewer', 'auditor'];

const NAV_GROUPS: NavGroup[] = [
  {
    label: 'Workspace',
    items: [{ to: '/cases', label: 'Cases', icon: FolderOpen, roles: ['analyst', 'reviewer'] }],
  },
  {
    label: 'Governance',
    items: [
      { to: '/admin/masters', label: 'Masters', icon: Settings2, roles: ['business_admin'] },
      { to: '/audit', label: 'Audit trail', icon: ShieldCheck, roles: ['auditor', 'business_admin'] },
    ],
  },
  {
    label: 'Account',
    items: [
      { to: '/admin/users', label: 'Users', icon: Users, roles: ['it_admin'] },
      { to: '/preferences', label: 'Preferences', icon: SlidersHorizontal, roles: ALL_ROLES },
    ],
  },
];

/** Up to two initials from a display name, for the top-bar avatar. */
function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return '?';
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

export function Shell() {
  const { user, hasRole, logout } = useAuth();
  const navigate = useNavigate();
  const { theme, toggle } = useTheme();

  const groups = NAV_GROUPS.map((g) => ({
    ...g,
    items: g.items.filter((item) => hasRole(...item.roles)),
  })).filter((g) => g.items.length > 0);

  const doLogout = () => {
    logout();
    navigate('/login', { replace: true });
  };

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark">CAM</span>
          <span className="brand-name">Studio</span>
        </div>

        <button type="button" className="topbar-search" aria-label="Global search">
          <Search size={15} strokeWidth={1.7} />
          <span className="topbar-search-ph">Search cases, CAMs, masters…</span>
          <span className="kbd">⌘K</span>
        </button>

        <div className="topbar-right">
          {user ? (
            <>
              <NotificationBell />
              <button
                type="button"
                className="icon-btn"
                onClick={toggle}
                title={theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme'}
                aria-label="Toggle color theme"
              >
                {theme === 'dark' ? <Sun size={17} strokeWidth={1.7} /> : <Moon size={17} strokeWidth={1.7} />}
              </button>
              <div className="topbar-user">
                <span className="topbar-user-name">{user.display_name}</span>
                <span className="topbar-user-role">{user.roles.map((r) => r.replace(/_/g, ' ')).join(' · ')}</span>
              </div>
              <span className="avatar" aria-hidden="true">
                {initials(user.display_name)}
              </span>
              <button type="button" className="icon-btn" onClick={doLogout} title="Log out" aria-label="Log out">
                <LogOut size={17} strokeWidth={1.7} />
              </button>
            </>
          ) : null}
        </div>
      </header>

      <div className="app-body">
        <nav className="sidenav">
          <div className="sidenav-groups">
            {groups.map((group) => (
              <div key={group.label} className="nav-group">
                <div className="nav-group-label">{group.label}</div>
                {group.items.map((item) => {
                  const Icon = item.icon;
                  return (
                    <NavLink
                      key={item.to}
                      to={item.to}
                      className={({ isActive }) => (isActive ? 'nav-link active' : 'nav-link')}
                    >
                      <span className="nav-ico" aria-hidden="true">
                        <Icon size={18} strokeWidth={1.7} />
                      </span>
                      {item.label}
                    </NavLink>
                  );
                })}
              </div>
            ))}
          </div>
          <div className="sidenav-foot mono">v1.0 · bank-grade</div>
        </nav>
        <main className="main-area">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
