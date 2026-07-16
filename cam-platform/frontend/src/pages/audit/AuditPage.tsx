import { Fragment, useCallback, useEffect, useState } from 'react';
import { api, errorMessage } from '../../api/client';
import { AUDIT_ACTIONS } from '../../api/types';
import type { AuditEvent, AuditEventsPage, ChainVerification } from '../../api/types';
import { EmptyState } from '../../components/EmptyState';
import { Spinner } from '../../components/Spinner';
import { useToast } from '../../components/Toast';
import { LineagePanel } from './LineagePanel';

const PAGE_SIZE = 50;

interface Filters {
  entity_type: string;
  entity_id: string;
  action: string;
  case_id: string;
  actor: string;
}

const EMPTY_FILTERS: Filters = { entity_type: '', entity_id: '', action: '', case_id: '', actor: '' };

export function AuditPage() {
  const toast = useToast();
  const [filters, setFilters] = useState<Filters>(EMPTY_FILTERS);
  const [applied, setApplied] = useState<Filters>(EMPTY_FILTERS);
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<AuditEventsPage | null>(null);
  const [loading, setLoading] = useState(false);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [chain, setChain] = useState<ChainVerification | null>(null);
  const [verifying, setVerifying] = useState(false);

  const load = useCallback(
    async (f: Filters, off: number) => {
      setLoading(true);
      try {
        const qs = new URLSearchParams();
        if (f.entity_type.trim()) qs.set('entity_type', f.entity_type.trim());
        if (f.entity_id.trim()) qs.set('entity_id', f.entity_id.trim());
        if (f.action.trim()) qs.set('action', f.action.trim());
        if (f.case_id.trim()) qs.set('case_id', f.case_id.trim());
        if (f.actor.trim()) qs.set('actor', f.actor.trim());
        qs.set('limit', String(PAGE_SIZE));
        qs.set('offset', String(off));
        setPage(await api.get<AuditEventsPage>(`/api/audit/events?${qs.toString()}`));
      } catch (err) {
        toast.error(errorMessage(err));
        setPage({ events: [], total: 0 });
      } finally {
        setLoading(false);
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );

  useEffect(() => {
    load(applied, offset);
  }, [load, applied, offset]);

  const applyFilters = () => {
    setOffset(0);
    setApplied({ ...filters });
  };

  const exportAudit = async (format: 'csv' | 'json') => {
    try {
      const qs = new URLSearchParams({ format });
      if (applied.case_id.trim()) qs.set('case_id', applied.case_id.trim());
      await api.download(`/api/audit/export?${qs.toString()}`, `audit-export.${format}`);
      toast.success(`Audit trail exported (${format.toUpperCase()})`);
    } catch (err) {
      toast.error(errorMessage(err));
    }
  };

  const verifyChain = async () => {
    setVerifying(true);
    setChain(null);
    try {
      setChain(await api.get<ChainVerification>('/api/audit/verify-chain'));
    } catch (err) {
      toast.error(errorMessage(err));
    } finally {
      setVerifying(false);
    }
  };

  const total = page?.total ?? 0;
  const pageStart = total === 0 ? 0 : offset + 1;
  const pageEnd = Math.min(offset + PAGE_SIZE, total);

  return (
    <div className="page page-wide">
      <div className="page-head">
        <h1>Audit trail</h1>
        <div className="btn-row">
          <button type="button" className="btn" onClick={() => exportAudit('csv')}>
            Export CSV
          </button>
          <button type="button" className="btn" onClick={() => exportAudit('json')}>
            Export JSON
          </button>
          <button type="button" className="btn" disabled={verifying} onClick={verifyChain}>
            {verifying ? 'Verifying…' : 'Verify chain'}
          </button>
        </div>
      </div>

      {chain ? (
        <div className={`banner ${chain.intact ? 'banner-info' : 'banner-error'}`}>
          {chain.intact
            ? `Hash chain intact — ${chain.checked} events verified.`
            : `Hash chain BROKEN at seq ${chain.first_break_seq} (checked ${chain.checked} events). Possible tampering.`}
        </div>
      ) : null}

      <div className="card">
        <div className="filter-bar">
          <input
            className="input"
            placeholder="entity_type (e.g. cam, run, document)"
            value={filters.entity_type}
            onChange={(e) => setFilters({ ...filters, entity_type: e.target.value })}
          />
          <input
            className="input"
            placeholder="entity_id"
            value={filters.entity_id}
            onChange={(e) => setFilters({ ...filters, entity_id: e.target.value })}
          />
          <input
            className="input"
            list="audit-actions"
            placeholder="action"
            value={filters.action}
            onChange={(e) => setFilters({ ...filters, action: e.target.value })}
          />
          <datalist id="audit-actions">
            {AUDIT_ACTIONS.map((a) => (
              <option key={a} value={a} />
            ))}
          </datalist>
          <input
            className="input"
            placeholder="case_id"
            value={filters.case_id}
            onChange={(e) => setFilters({ ...filters, case_id: e.target.value })}
          />
          <input
            className="input"
            placeholder="actor"
            value={filters.actor}
            onChange={(e) => setFilters({ ...filters, actor: e.target.value })}
            onKeyDown={(e) => e.key === 'Enter' && applyFilters()}
          />
          <button type="button" className="btn btn-primary" onClick={applyFilters}>
            Apply
          </button>
          <button
            type="button"
            className="btn"
            onClick={() => {
              setFilters(EMPTY_FILTERS);
              setApplied(EMPTY_FILTERS);
              setOffset(0);
            }}
          >
            Clear
          </button>
        </div>

        {loading || !page ? (
          <Spinner label="Loading events…" />
        ) : page.events.length === 0 ? (
          <EmptyState title="No audit events match" hint="Adjust the filters or clear them to see the full trail." />
        ) : (
          <>
            <div className="table-wrap">
              <table className="table audit-table">
                <thead>
                  <tr>
                    <th>Time</th>
                    <th>Actor</th>
                    <th>Action</th>
                    <th>Entity</th>
                    <th>Case / Run / CAM</th>
                    <th>Correlation</th>
                  </tr>
                </thead>
                <tbody>
                  {page.events.map((e: AuditEvent) => (
                    <Fragment key={e.id}>
                      <tr
                        className="row-clickable"
                        onClick={() => setExpanded(expanded === e.id ? null : e.id)}
                      >
                        <td className="muted">{new Date(e.ts).toLocaleString()}</td>
                        <td>
                          {e.actor} <span className="muted">({e.actor_roles.join(', ')})</span>
                        </td>
                        <td>
                          <span className="chip chip-navy">{e.action}</span>
                        </td>
                        <td>
                          {e.entity_type} <span className="muted mono">{e.entity_id.slice(0, 8)}…</span>
                        </td>
                        <td className="mono muted">
                          {[e.case_id, e.run_id, e.cam_id]
                            .map((id) => (id ? `${id.slice(0, 8)}…` : '—'))
                            .join(' / ')}
                        </td>
                        <td className="mono muted">{e.correlation_id.slice(0, 8)}…</td>
                      </tr>
                      {expanded === e.id ? (
                        <tr className="detail-row">
                          <td colSpan={6}>
                            <pre className="detail-json">
                              {JSON.stringify(
                                {
                                  seq: e.seq,
                                  entity_id: e.entity_id,
                                  case_id: e.case_id,
                                  run_id: e.run_id,
                                  cam_id: e.cam_id,
                                  correlation_id: e.correlation_id,
                                  detail: e.detail,
                                  prev_hash: e.prev_hash,
                                  hash: e.hash,
                                },
                                null,
                                2,
                              )}
                            </pre>
                          </td>
                        </tr>
                      ) : null}
                    </Fragment>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="pagination">
              <span className="muted">
                {pageStart}–{pageEnd} of {total}
              </span>
              <button type="button" className="btn btn-sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>
                ← Previous
              </button>
              <button
                type="button"
                className="btn btn-sm"
                disabled={offset + PAGE_SIZE >= total}
                onClick={() => setOffset(offset + PAGE_SIZE)}
              >
                Next →
              </button>
            </div>
          </>
        )}
      </div>

      <LineagePanel />
    </div>
  );
}
