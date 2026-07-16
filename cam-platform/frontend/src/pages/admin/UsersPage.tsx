import { useCallback, useEffect, useState } from 'react';
import { api, errorMessage } from '../../api/client';
import { ALL_ROLES } from '../../api/types';
import type { User } from '../../api/types';
import { Modal } from '../../components/Modal';
import { PageLoading } from '../../components/Spinner';
import { StatusChip } from '../../components/StatusChip';
import { useToast } from '../../components/Toast';
import { DataTable } from '../../components/DataTable';

interface CreateForm {
  username: string;
  display_name: string;
  email: string;
  password: string;
  roles: string[];
}

const EMPTY_FORM: CreateForm = { username: '', display_name: '', email: '', password: '', roles: [] };

export function UsersPage() {
  const toast = useToast();
  const [users, setUsers] = useState<User[] | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [form, setForm] = useState<CreateForm>(EMPTY_FORM);
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState<User | null>(null);
  const [editRoles, setEditRoles] = useState<string[]>([]);

  const load = useCallback(() => {
    api
      .get<User[]>('/api/auth/users')
      .then(setUsers)
      .catch((err) => toast.error(errorMessage(err)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(load, [load]);

  const create = async () => {
    setBusy(true);
    try {
      await api.post<User>('/api/auth/users', form);
      toast.success(`User ${form.username} created`);
      setShowCreate(false);
      setForm(EMPTY_FORM);
      load();
    } catch (err) {
      toast.error(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  const toggleActive = async (u: User) => {
    try {
      await api.patch<User>(`/api/auth/users/${u.id}`, { active: !u.active });
      toast.success(`${u.username} ${u.active ? 'deactivated' : 'activated'}`);
      load();
    } catch (err) {
      toast.error(errorMessage(err));
    }
  };

  const saveRoles = async () => {
    if (!editing) return;
    setBusy(true);
    try {
      await api.patch<User>(`/api/auth/users/${editing.id}`, { roles: editRoles });
      toast.success(`Roles updated for ${editing.username}`);
      setEditing(null);
      load();
    } catch (err) {
      toast.error(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  const toggleRole = (roles: string[], role: string, set: (r: string[]) => void) => {
    set(roles.includes(role) ? roles.filter((r) => r !== role) : [...roles, role]);
  };

  if (!users) return <PageLoading label="Loading users…" />;

  return (
    <div className="page">
      <div className="page-head">
        <h1>User administration</h1>
        <button type="button" className="btn btn-primary" onClick={() => setShowCreate(true)}>
          New user
        </button>
      </div>
      <div className="card">
        <DataTable
          rows={users}
          rowKey={(u) => u.id}
          emptyTitle="No users"
          columns={[
            { header: 'Username', render: (u) => <span className="mono">{u.username}</span> },
            { header: 'Display name', render: (u) => u.display_name },
            { header: 'Email', render: (u) => u.email },
            {
              header: 'Roles',
              render: (u) => (
                <span className="chip-row">
                  {u.roles.map((r) => (
                    <span key={r} className="chip chip-navy">
                      {r.replace(/_/g, ' ')}
                    </span>
                  ))}
                </span>
              ),
            },
            { header: 'Status', render: (u) => <StatusChip status={u.active ? 'active' : 'inactive'} /> },
            {
              header: 'Actions',
              render: (u) => (
                <span className="btn-row">
                  <button
                    type="button"
                    className="btn btn-sm"
                    onClick={() => {
                      setEditing(u);
                      setEditRoles(u.roles);
                    }}
                  >
                    Edit roles
                  </button>
                  <button type="button" className={`btn btn-sm${u.active ? ' btn-danger' : ''}`} onClick={() => toggleActive(u)}>
                    {u.active ? 'Deactivate' : 'Activate'}
                  </button>
                </span>
              ),
            },
          ]}
        />
      </div>

      {showCreate ? (
        <Modal
          title="Create user"
          onClose={() => setShowCreate(false)}
          footer={
            <>
              <button type="button" className="btn" onClick={() => setShowCreate(false)}>
                Cancel
              </button>
              <button
                type="button"
                className="btn btn-primary"
                disabled={busy || !form.username || !form.display_name || !form.email || !form.password || form.roles.length === 0}
                onClick={create}
              >
                {busy ? 'Creating…' : 'Create user'}
              </button>
            </>
          }
        >
          <div className="field">
            <label>Username</label>
            <input className="input" value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} />
          </div>
          <div className="field">
            <label>Display name</label>
            <input className="input" value={form.display_name} onChange={(e) => setForm({ ...form, display_name: e.target.value })} />
          </div>
          <div className="field">
            <label>Email</label>
            <input className="input" type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} />
          </div>
          <div className="field">
            <label>Password</label>
            <input className="input" type="password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} />
          </div>
          <div className="field">
            <label>Roles</label>
            <div className="check-row">
              {ALL_ROLES.map((r) => (
                <label key={r} className="check-pill">
                  <input
                    type="checkbox"
                    checked={form.roles.includes(r)}
                    onChange={() => toggleRole(form.roles, r, (roles) => setForm({ ...form, roles }))}
                  />
                  {r.replace(/_/g, ' ')}
                </label>
              ))}
            </div>
          </div>
        </Modal>
      ) : null}

      {editing ? (
        <Modal
          title={`Edit roles — ${editing.username}`}
          onClose={() => setEditing(null)}
          footer={
            <>
              <button type="button" className="btn" onClick={() => setEditing(null)}>
                Cancel
              </button>
              <button type="button" className="btn btn-primary" disabled={busy || editRoles.length === 0} onClick={saveRoles}>
                {busy ? 'Saving…' : 'Save roles'}
              </button>
            </>
          }
        >
          <div className="check-row">
            {ALL_ROLES.map((r) => (
              <label key={r} className="check-pill">
                <input type="checkbox" checked={editRoles.includes(r)} onChange={() => toggleRole(editRoles, r, setEditRoles)} />
                {r.replace(/_/g, ' ')}
              </label>
            ))}
          </div>
        </Modal>
      ) : null}
    </div>
  );
}
