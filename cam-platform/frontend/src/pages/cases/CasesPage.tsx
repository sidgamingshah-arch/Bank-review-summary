import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api, errorMessage } from '../../api/client';
import type { Case, CaseRelationship, CaseSegment, ItemSummary } from '../../api/types';
import { useAuth } from '../../auth/AuthContext';
import { DataTable } from '../../components/DataTable';
import { Modal } from '../../components/Modal';
import { PageLoading } from '../../components/Spinner';
import { StatusChip } from '../../components/StatusChip';
import { useToast } from '../../components/Toast';

interface NewCaseForm {
  borrower_name: string;
  segment: CaseSegment;
  relationship: CaseRelationship;
  industry_code: string;
}

const EMPTY: NewCaseForm = { borrower_name: '', segment: 'corporate', relationship: 'etb', industry_code: '' };

export function CasesPage() {
  const toast = useToast();
  const navigate = useNavigate();
  const { hasRole } = useAuth();
  const [cases, setCases] = useState<Case[] | null>(null);
  const [showNew, setShowNew] = useState(false);
  const [form, setForm] = useState<NewCaseForm>(EMPTY);
  const [industries, setIndustries] = useState<ItemSummary[] | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api
      .get<Case[]>('/api/cases')
      .then(setCases)
      .catch((err) => toast.error(errorMessage(err)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const openNew = async () => {
    setForm(EMPTY);
    setShowNew(true);
    if (industries === null) {
      try {
        const items = await api.get<ItemSummary[]>('/api/masters/industries');
        setIndustries(items.filter((i) => i.published_version !== null));
      } catch {
        setIndustries([]); // fall back to free-text industry input
      }
    }
  };

  const create = async () => {
    setBusy(true);
    try {
      const created = await api.post<Case>('/api/cases', form);
      toast.success(`Case created for ${created.borrower_name}`);
      setShowNew(false);
      navigate(`/cases/${created.id}`);
    } catch (err) {
      toast.error(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  if (!cases) return <PageLoading label="Loading cases…" />;

  const publishedIndustries = industries ?? [];

  return (
    <div className="page">
      <div className="page-head">
        <h1>Cases</h1>
        {hasRole('analyst') ? (
          <button type="button" className="btn btn-primary" onClick={openNew}>
            New case
          </button>
        ) : null}
      </div>
      <div className="card">
        <DataTable
          rows={cases}
          rowKey={(c) => c.id}
          onRowClick={(c) => navigate(`/cases/${c.id}`)}
          emptyTitle="No cases yet"
          emptyHint={hasRole('analyst') ? 'Create your first case to start assembling a CAM.' : 'Cases will appear here once analysts create them.'}
          columns={[
            { header: 'Borrower', render: (c) => <strong>{c.borrower_name}</strong> },
            { header: 'Segment', render: (c) => c.segment.replace(/_/g, ' ') },
            { header: 'Relationship', render: (c) => c.relationship.toUpperCase() },
            { header: 'Industry', render: (c) => <span className="mono">{c.industry_code}</span> },
            { header: 'Status', render: (c) => <StatusChip status={c.status} /> },
            { header: 'Created', render: (c) => new Date(c.created_at).toLocaleString() },
          ]}
        />
      </div>

      {showNew ? (
        <Modal
          title="New case"
          onClose={() => setShowNew(false)}
          footer={
            <>
              <button type="button" className="btn" onClick={() => setShowNew(false)}>
                Cancel
              </button>
              <button
                type="button"
                className="btn btn-primary"
                disabled={busy || !form.borrower_name.trim() || !form.industry_code}
                onClick={create}
              >
                {busy ? 'Creating…' : 'Create case'}
              </button>
            </>
          }
        >
          <div className="field">
            <label>Borrower name</label>
            <input className="input" value={form.borrower_name} onChange={(e) => setForm({ ...form, borrower_name: e.target.value })} autoFocus />
          </div>
          <div className="form-grid-2">
            <div className="field">
              <label>Segment</label>
              <select className="select" value={form.segment} onChange={(e) => setForm({ ...form, segment: e.target.value as CaseSegment })}>
                <option value="corporate">Corporate</option>
                <option value="fi">Financial institution</option>
                <option value="project_finance">Project finance</option>
              </select>
            </div>
            <div className="field">
              <label>Relationship</label>
              <select
                className="select"
                value={form.relationship}
                onChange={(e) => setForm({ ...form, relationship: e.target.value as CaseRelationship })}
              >
                <option value="etb">ETB (existing to bank)</option>
                <option value="ntb">NTB (new to bank)</option>
              </select>
            </div>
          </div>
          <div className="field">
            <label>Industry</label>
            {industries === null ? (
              <div className="muted">Loading industries…</div>
            ) : publishedIndustries.length > 0 ? (
              <select className="select" value={form.industry_code} onChange={(e) => setForm({ ...form, industry_code: e.target.value })}>
                <option value="">Select industry…</option>
                {publishedIndustries.map((i) => (
                  <option key={i.key} value={i.key}>
                    {i.key}
                  </option>
                ))}
              </select>
            ) : (
              <>
                <input
                  className="input"
                  placeholder="industry_code (no published industries found)"
                  value={form.industry_code}
                  onChange={(e) => setForm({ ...form, industry_code: e.target.value })}
                />
                <div className="hint">No published industry master entries — enter the industry code manually.</div>
              </>
            )}
          </div>
        </Modal>
      ) : null}
    </div>
  );
}
