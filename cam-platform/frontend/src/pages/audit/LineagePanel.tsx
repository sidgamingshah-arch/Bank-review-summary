import { useState } from 'react';
import { api, errorMessage } from '../../api/client';
import type { AuditEvent, CamLineage } from '../../api/types';
import { Spinner } from '../../components/Spinner';
import { useToast } from '../../components/Toast';

function KvBlock({ data }: { data: Record<string, unknown> }) {
  return (
    <dl className="kv">
      {Object.entries(data).map(([k, v]) => (
        <span key={k} className="kv-pair">
          <dt>{k.replace(/_/g, ' ')}</dt>
          <dd className="mono">{typeof v === 'object' && v !== null ? JSON.stringify(v) : String(v)}</dd>
        </span>
      ))}
    </dl>
  );
}

function isEventArray(v: unknown): v is AuditEvent[] {
  return Array.isArray(v) && (v.length === 0 || (typeof v[0] === 'object' && v[0] !== null && 'action' in v[0]));
}

export function LineagePanel() {
  const toast = useToast();
  const [camId, setCamId] = useState('');
  const [lineage, setLineage] = useState<CamLineage | null>(null);
  const [busy, setBusy] = useState(false);

  const lookup = async () => {
    setBusy(true);
    setLineage(null);
    try {
      setLineage(await api.get<CamLineage>(`/api/audit/lineage/cam/${encodeURIComponent(camId.trim())}`));
    } catch (err) {
      toast.error(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  const runRecord = lineage
    ? ((lineage.run ?? lineage.run_record) as Record<string, unknown> | null | undefined)
    : null;
  const events = lineage && isEventArray(lineage.events) ? lineage.events : null;
  const extraKeys = lineage
    ? Object.entries(lineage).filter(([k]) => !['run', 'run_record', 'events'].includes(k))
    : [];

  return (
    <div className="card">
      <div className="card-head">
        <h2>CAM lineage</h2>
      </div>
      <div className="filter-bar">
        <input
          className="input"
          placeholder="cam_id (UUID)"
          value={camId}
          onChange={(e) => setCamId(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && camId.trim() && lookup()}
        />
        <button type="button" className="btn btn-primary" disabled={!camId.trim() || busy} onClick={lookup}>
          {busy ? 'Loading…' : 'Trace lineage'}
        </button>
      </div>
      {busy ? <Spinner label="Fetching lineage…" /> : null}
      {lineage ? (
        <div className="lineage">
          {runRecord ? (
            <div className="lineage-run">
              <h4>Run record — masters, model, preferences &amp; document hashes</h4>
              <KvBlock data={runRecord} />
            </div>
          ) : null}
          {events ? (
            <div className="lineage-events">
              <h4>Chronology ({events.length} events)</h4>
              <ol className="event-timeline">
                {events.map((e) => (
                  <li key={e.id ?? `${e.seq}`}>
                    <span className="muted mono">{e.ts ? new Date(e.ts).toLocaleString() : ''}</span>{' '}
                    <span className="chip chip-navy">{e.action}</span> <strong>{e.actor}</strong>{' '}
                    <span className="muted">
                      {e.entity_type}
                      {e.entity_id ? ` ${String(e.entity_id).slice(0, 8)}…` : ''}
                    </span>
                    {e.detail && Object.keys(e.detail).length > 0 ? (
                      <pre className="detail-json slim">{JSON.stringify(e.detail, null, 2)}</pre>
                    ) : null}
                  </li>
                ))}
              </ol>
            </div>
          ) : null}
          {!runRecord && !events ? (
            <pre className="detail-json">{JSON.stringify(lineage, null, 2)}</pre>
          ) : extraKeys.length > 0 ? (
            <KvBlock data={Object.fromEntries(extraKeys)} />
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
