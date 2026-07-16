import { useCallback, useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { api, errorMessage } from '../../api/client';
import type { Case, CaseDocument, CamSummary, ItemSummary, RunSummary } from '../../api/types';
import { PageLoading } from '../../components/Spinner';
import { StatusChip } from '../../components/StatusChip';
import { useToast } from '../../components/Toast';
import { EmptyState } from '../../components/EmptyState';
import { DocumentsCard } from './DocumentsCard';
import { GenerationCard } from './GenerationCard';

export function CaseDetailPage() {
  const { caseId = '' } = useParams();
  const toast = useToast();
  const [caseRec, setCaseRec] = useState<Case | null>(null);
  const [documents, setDocuments] = useState<CaseDocument[] | null>(null);
  const [doctypes, setDoctypes] = useState<string[]>([]);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [cams, setCams] = useState<CamSummary[]>([]);
  const [notFound, setNotFound] = useState(false);

  const reloadDocuments = useCallback(async () => {
    try {
      setDocuments(await api.get<CaseDocument[]>(`/api/cases/${caseId}/documents`));
    } catch (err) {
      toast.error(errorMessage(err));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [caseId]);

  const reloadRunsAndCams = useCallback(async () => {
    // Shapes of these list endpoints are conservative subsets — see README assumptions.
    try {
      setRuns(await api.get<RunSummary[]>(`/api/runs?case_id=${encodeURIComponent(caseId)}`));
    } catch {
      setRuns([]);
    }
    try {
      setCams(await api.get<CamSummary[]>(`/api/cams?case_id=${encodeURIComponent(caseId)}`));
    } catch {
      setCams([]);
    }
  }, [caseId]);

  useEffect(() => {
    let cancelled = false;
    api
      .get<Case>(`/api/cases/${caseId}`)
      .then((c) => {
        if (!cancelled) setCaseRec(c);
      })
      .catch((err) => {
        if (!cancelled) {
          setNotFound(true);
          toast.error(errorMessage(err));
        }
      });
    reloadDocuments();
    reloadRunsAndCams();
    api
      .get<ItemSummary[]>('/api/masters/doctypes')
      .then((items) => {
        if (!cancelled) setDoctypes(items.filter((i) => i.published_version !== null).map((i) => i.key));
      })
      .catch(() => {
        if (!cancelled) setDoctypes([]);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [caseId]);

  if (notFound) {
    return (
      <div className="page">
        <EmptyState title="Case not found" action={<Link to="/cases" className="btn">Back to cases</Link>} />
      </div>
    );
  }
  if (!caseRec || documents === null) return <PageLoading label="Loading case…" />;

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <div className="breadcrumbs">
            <Link to="/cases">Cases</Link> / <span>{caseRec.borrower_name}</span>
          </div>
          <h1>{caseRec.borrower_name}</h1>
          <div className="chip-row head-chips">
            <StatusChip status={caseRec.status} />
            <span className="chip chip-gray">{caseRec.segment.replace(/_/g, ' ')}</span>
            <span className="chip chip-gray">{caseRec.relationship.toUpperCase()}</span>
            <span className="chip chip-gray mono">{caseRec.industry_code}</span>
          </div>
        </div>
      </div>

      <div className="case-grid">
        <div className="case-col-main">
          <DocumentsCard caseId={caseId} documents={documents} doctypes={doctypes} onReload={reloadDocuments} />
        </div>
        <div className="case-col-side">
          <GenerationCard caseId={caseId} onRunStarted={reloadRunsAndCams} />

          <div className="card">
            <div className="card-head">
              <h2>Runs &amp; CAMs</h2>
            </div>
            {runs.length === 0 && cams.length === 0 ? (
              <p className="muted">No generation runs yet for this case.</p>
            ) : (
              <>
                {runs.map((r) => (
                  <div key={r.id} className="mini-row">
                    <StatusChip status={r.status} />
                    <Link to={`/runs/${r.id}`} className="mono">
                      run {r.id.slice(0, 8)}…
                    </Link>
                    <span className="muted">{r.template_key ?? ''}</span>
                    {r.cam_id ? (
                      <Link to={`/cams/${r.cam_id}`} className="btn btn-sm">
                        Open CAM
                      </Link>
                    ) : null}
                  </div>
                ))}
                {cams.map((c) => (
                  <div key={c.id} className="mini-row">
                    <StatusChip status={c.status} />
                    <Link to={`/cams/${c.id}`}>{c.title ?? `CAM ${c.id.slice(0, 8)}…`}</Link>
                  </div>
                ))}
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
