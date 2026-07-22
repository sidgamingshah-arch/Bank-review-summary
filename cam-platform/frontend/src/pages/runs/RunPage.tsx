import { useCallback, useEffect, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { api, errorMessage } from '../../api/client';
import type { Run } from '../../api/types';
import { PageLoading } from '../../components/Spinner';
import { StatusChip } from '../../components/StatusChip';
import { useToast } from '../../components/Toast';
import { EmptyState } from '../../components/EmptyState';

const POLL_MS = 1500;

export function RunPage() {
  const { runId = '' } = useParams();
  const toast = useToast();
  const navigate = useNavigate();
  const [run, setRun] = useState<Run | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [recordOpen, setRecordOpen] = useState(false);
  const [retrying, setRetrying] = useState<string | null>(null);

  const fetchRun = useCallback(async (): Promise<void> => {
    try {
      setRun(await api.get<Run>(`/api/runs/${runId}`));
    } catch (err) {
      setNotFound(true);
      toast.error(errorMessage(err));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId]);

  useEffect(() => {
    fetchRun();
  }, [fetchRun]);

  // Self-sustaining poll: whenever the run (or any section) is still active,
  // schedule the next fetch. Retries automatically resume polling this way.
  const isActive =
    run !== null &&
    (run.status === 'queued' ||
      run.status === 'running' ||
      run.sections.some((s) => s.status === 'queued' || s.status === 'running'));

  useEffect(() => {
    if (!isActive) return;
    const t = window.setTimeout(fetchRun, POLL_MS);
    return () => window.clearTimeout(t);
  }, [run, isActive, fetchRun]);

  const retrySection = async (sectionCode: string) => {
    setRetrying(sectionCode);
    try {
      await api.post(`/api/runs/${runId}/sections/${encodeURIComponent(sectionCode)}/retry`);
      toast.success(`Section ${sectionCode} queued for retry`);
      await fetchRun();
    } catch (err) {
      toast.error(errorMessage(err));
    } finally {
      setRetrying(null);
    }
  };

  if (notFound) {
    return (
      <div className="page">
        <EmptyState title="Run not found" action={<Link to="/cases" className="btn">Back to cases</Link>} />
      </div>
    );
  }
  if (!run) return <PageLoading label="Loading run…" />;

  const sections = [...run.sections].sort((a, b) => a.order - b.order);
  const total = sections.length;
  const doneCount = sections.filter((s) => s.status === 'complete' || s.status === 'skipped').length;
  const pct = total === 0 ? 0 : Math.round((doneCount / total) * 100);
  const live = run.status === 'queued' || run.status === 'running';

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <div className="breadcrumbs">
            <Link to="/cases">Cases</Link> / <Link to={`/cases/${run.case_id}`}>case</Link> / <span>run</span>
          </div>
          <h1>Generation run</h1>
          <div className="chip-row head-chips">
            <StatusChip status={run.status} />
            <span className="chip chip-gray mono">{run.template_key}</span>
            {live ? <span className="muted">refreshing every {POLL_MS / 1000}s…</span> : null}
          </div>
        </div>
        {run.cam_id ? (
          <button type="button" className="btn btn-primary btn-lg" onClick={() => navigate(`/cams/${run.cam_id}`)}>
            Open CAM workspace →
          </button>
        ) : null}
      </div>

      <div className="card">
        <div className="progress-line">
          <div className="progress">
            <div className="progress-fill" style={{ width: `${pct}%` }} />
          </div>
          <span className="muted">
            {doneCount} / {total} sections
          </span>
        </div>

        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th style={{ width: '3rem' }}>#</th>
                <th>Section</th>
                <th>Status</th>
                <th>Attempts</th>
                <th>Tokens (in/out)</th>
                <th>Notes</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {sections.map((s) => (
                <tr key={s.section_code}>
                  <td className="muted">{s.order}</td>
                  <td>
                    <strong>{s.name}</strong> <span className="muted mono">{s.section_code}</span>
                  </td>
                  <td>
                    <StatusChip status={s.status} />
                  </td>
                  <td>{s.attempts}</td>
                  <td className="mono">
                    {s.tokens_in} / {s.tokens_out}
                  </td>
                  <td>
                    {s.error ? <span className="error-text">{s.error}</span> : null}
                    {s.untraceable.length > 0 ? (
                      <span
                        className="chip chip-amber"
                        title={`Untraceable numbers (not found in source documents):\n${s.untraceable.join(', ')}`}
                      >
                        ⚠ {s.untraceable.length} untraceable
                      </span>
                    ) : null}
                    {Object.entries(s.checks ?? {}).map(([agent, check]) => (
                      <span
                        key={agent}
                        className={`chip ${
                          check.passed === true ? 'chip-green' : check.passed === false ? 'chip-red' : 'chip-gray'
                        }`}
                        title={[
                          `${agent} check agent — ${
                            check.passed === true ? 'passed' : check.passed === false ? 'FAILED' : 'no verdict'
                          }${check.revisions ? ` after ${check.revisions} revision(s)` : ''}`,
                          ...(check.omissions ?? []),
                          ...(check.inconsistencies ?? []),
                        ].join('\n')}
                      >
                        {check.passed === true ? '✓' : check.passed === false ? '✗' : '·'} {agent}
                      </span>
                    ))}
                  </td>
                  <td>
                    {s.status === 'failed' ? (
                      <button
                        type="button"
                        className="btn btn-sm"
                        disabled={retrying === s.section_code}
                        onClick={() => retrySection(s.section_code)}
                      >
                        {retrying === s.section_code ? 'Retrying…' : 'Retry'}
                      </button>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {run.gaps.length > 0 ? (
        <div className="card">
          <div className="card-head">
            <h2>Data gaps</h2>
          </div>
          <div className="banner banner-warn slim">
            This run proceeded with missing inputs; a gap disclosure section is appended to the CAM.
          </div>
          <ul className="gap-list">
            {run.gaps.map((g) => (
              <li key={g.doctype_code}>
                <span className="chip chip-amber mono">{g.doctype_code}</span> {g.reason}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <div className="card">
        <button type="button" className="collapsible-toggle" onClick={() => setRecordOpen(!recordOpen)}>
          {recordOpen ? '▾' : '▸'} Run record (audit)
        </button>
        {recordOpen ? (
          <div className="collapsible-body">
            <dl className="kv">
              <dt>Run ID</dt>
              <dd className="mono">{run.id}</dd>
              <dt>Correlation</dt>
              <dd className="mono">{run.correlation_id}</dd>
              <dt>Model</dt>
              <dd className="mono">{run.model_identity}</dd>
              <dt>Started by</dt>
              <dd>
                {run.created_by} at {new Date(run.created_at).toLocaleString()}
              </dd>
              <dt>Applied preferences</dt>
              <dd>
                tonality={run.applied_preferences.tonality} · structure={run.applied_preferences.structure_bias} · tables=
                {run.applied_preferences.table_usage} · length={run.applied_preferences.length}{' '}
                <StatusChip status={run.applied_preferences.source} label={`source: ${run.applied_preferences.source}`} />
              </dd>
              <dt>Template version</dt>
              <dd className="mono">v{run.master_versions.template}</dd>
              <dt>Global rules</dt>
              <dd className="mono">{run.master_versions.global_rules != null ? `v${run.master_versions.global_rules}` : '—'}</dd>
              <dt>KPI set</dt>
              <dd className="mono">{run.master_versions.kpi_set != null ? `v${run.master_versions.kpi_set}` : '—'}</dd>
              <dt>Prompt versions</dt>
              <dd className="mono">
                {Object.entries(run.master_versions.prompts)
                  .map(([code, v]) => `${code}=v${v}`)
                  .join(', ') || '—'}
              </dd>
              <dt>Doctype versions</dt>
              <dd className="mono">
                {Object.entries(run.master_versions.doctypes)
                  .map(([code, v]) => `${code}=v${v}`)
                  .join(', ') || '—'}
              </dd>
            </dl>
          </div>
        ) : null}
      </div>
    </div>
  );
}
