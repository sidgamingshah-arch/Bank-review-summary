import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { AlertTriangle, Bell, CheckCircle2, XCircle } from 'lucide-react';
import { api } from '../api/client';
import type { AppNotification, NotificationsResponse } from '../api/types';

const POLL_MS = 30_000;

function NotifIcon({ kind }: { kind: string }) {
  if (kind === 'run_failed') return <XCircle size={17} strokeWidth={1.8} className="notif-ico-bad" />;
  if (kind === 'run_partial') return <AlertTriangle size={17} strokeWidth={1.8} className="notif-ico-warn" />;
  return <CheckCircle2 size={17} strokeWidth={1.8} className="notif-ico-ok" />;
}

/** Header bell: polls the caller's in-app notifications, shows an unread count,
 *  and opens a panel linking each completed run to its run screen. */
export function NotificationBell() {
  const navigate = useNavigate();
  const [items, setItems] = useState<AppNotification[]>([]);
  const [unread, setUnread] = useState(0);
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await api.get<NotificationsResponse>('/api/notifications?limit=30');
      setItems(res.notifications);
      setUnread(res.unread);
    } catch {
      /* notifications are non-critical — stay silent on transient errors */
    }
  }, []);

  useEffect(() => {
    load();
    const t = window.setInterval(load, POLL_MS);
    return () => window.clearInterval(t);
  }, [load]);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, [open]);

  const openNotification = async (n: AppNotification) => {
    setOpen(false);
    if (!n.read) {
      setItems((xs) => xs.map((x) => (x.id === n.id ? { ...x, read: true } : x)));
      setUnread((u) => Math.max(0, u - 1));
      try {
        await api.post(`/api/notifications/${n.id}/read`);
      } catch {
        /* optimistic; a failed mark-read self-heals on the next poll */
      }
    }
    if (n.run_id) navigate(`/runs/${n.run_id}`);
  };

  const markAll = async () => {
    setItems((xs) => xs.map((x) => ({ ...x, read: true })));
    setUnread(0);
    try {
      await api.post('/api/notifications/read-all');
    } catch {
      /* optimistic */
    }
  };

  return (
    <div className="notif" ref={ref}>
      <button
        type="button"
        className="icon-btn notif-bell"
        onClick={() => setOpen((o) => !o)}
        title="Notifications"
        aria-label={unread ? `Notifications, ${unread} unread` : 'Notifications'}
      >
        <Bell size={18} strokeWidth={1.7} />
        {unread > 0 ? <span className="notif-badge">{unread > 9 ? '9+' : unread}</span> : null}
      </button>
      {open ? (
        <div className="notif-panel" role="menu">
          <div className="notif-head">
            <strong>Notifications</strong>
            {unread > 0 ? (
              <button type="button" className="btn btn-sm btn-ghost" onClick={markAll}>
                Mark all read
              </button>
            ) : null}
          </div>
          {items.length === 0 ? (
            <div className="notif-empty">No notifications yet.</div>
          ) : (
            <ul className="notif-list">
              {items.map((n) => (
                <li key={n.id}>
                  <button
                    type="button"
                    className={`notif-item${n.read ? '' : ' unread'}`}
                    onClick={() => openNotification(n)}
                  >
                    <span className="notif-ico" aria-hidden="true">
                      <NotifIcon kind={n.kind} />
                    </span>
                    <span className="notif-text">
                      <span className="notif-title">{n.title}</span>
                      <span className="notif-body">{n.body}</span>
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      ) : null}
    </div>
  );
}
