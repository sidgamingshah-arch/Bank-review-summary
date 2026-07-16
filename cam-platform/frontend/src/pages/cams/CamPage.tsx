import { useCallback, useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { api, errorMessage } from '../../api/client';
import type { Cam } from '../../api/types';
import { ConfirmDialog } from '../../components/ConfirmDialog';
import { EmptyState } from '../../components/EmptyState';
import { PageLoading } from '../../components/Spinner';
import { useToast } from '../../components/Toast';
import { SectionView } from './SectionView';
import { ChatPanel } from './ChatPanel';

export function CamPage() {
  const { camId = '' } = useParams();
  const toast = useToast();
  const [cam, setCam] = useState<Cam | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [confirmFinalise, setConfirmFinalise] = useState(false);
  const [finalising, setFinalising] = useState(false);
  const [exporting, setExporting] = useState<'docx' | 'pdf' | null>(null);

  const reloadCam = useCallback(async () => {
    try {
      const c = await api.get<Cam>(`/api/cams/${camId}`);
      setCam(c);
      return c;
    } catch (err) {
      setNotFound(true);
      toast.error(errorMessage(err));
      return null;
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [camId]);

  useEffect(() => {
    reloadCam().then((c) => {
      if (c && c.sections.length > 0) {
        setSelectedId((cur) => cur ?? [...c.sections].sort((a, b) => a.order - b.order)[0].id);
      }
    });
  }, [reloadCam]);

  /** Local patch after an edit save, so the whole CAM is not refetched on every autosave. */
  const patchSection = useCallback((sectionId: string, content: string, versionNo: number) => {
    setCam((cur) =>
      cur
        ? {
            ...cur,
            sections: cur.sections.map((s) =>
              s.id === sectionId ? { ...s, content, current_version_no: versionNo } : s,
            ),
          }
        : cur,
    );
  }, []);

  const finalise = async () => {
    setFinalising(true);
    try {
      const updated = await api.post<Cam>(`/api/cams/${camId}/finalise`);
      setCam(updated);
      setConfirmFinalise(false);
      toast.success('CAM finalised — exports no longer carry the draft watermark');
    } catch (err) {
      toast.error(errorMessage(err));
    } finally {
      setFinalising(false);
    }
  };

  const exportCam = async (fmt: 'docx' | 'pdf') => {
    setExporting(fmt);
    try {
      await api.download(`/api/cams/${camId}/export.${fmt}`, `cam-${camId}.${fmt}`);
      toast.success(`Export (${fmt.toUpperCase()}) downloaded`);
    } catch (err) {
      toast.error(errorMessage(err));
    } finally {
      setExporting(null);
    }
  };

  if (notFound) {
    return (
      <div className="page">
        <EmptyState title="CAM not found" action={<Link to="/cases" className="btn">Back to cases</Link>} />
      </div>
    );
  }
  if (!cam) return <PageLoading label="Loading CAM…" />;

  const sections = [...cam.sections].sort((a, b) => a.order - b.order);
  const selected = sections.find((s) => s.id === selectedId) ?? sections[0] ?? null;
  const isDraft = cam.status === 'draft';

  return (
    <div className="cam-page">
      <div className="cam-header">
        <div className="cam-header-left">
          <div className="breadcrumbs">
            <Link to="/cases">Cases</Link> / <Link to={`/cases/${cam.case_id}`}>case</Link> /{' '}
            <Link to={`/runs/${cam.run_id}`}>run</Link> / <span>CAM</span>
          </div>
          <h1>{cam.title}</h1>
        </div>
        <div className="cam-header-right">
          {isDraft ? <span className="watermark-chip">AI-ASSISTED DRAFT</span> : <span className="final-chip">FINAL</span>}
          {isDraft ? (
            <button type="button" className="btn btn-primary" onClick={() => setConfirmFinalise(true)}>
              Finalise
            </button>
          ) : null}
          <button type="button" className="btn" disabled={exporting === 'docx'} onClick={() => exportCam('docx')}>
            {exporting === 'docx' ? 'Exporting…' : 'Export DOCX'}
          </button>
          <button type="button" className="btn" disabled={exporting === 'pdf'} onClick={() => exportCam('pdf')}>
            {exporting === 'pdf' ? 'Exporting…' : 'Export PDF'}
          </button>
        </div>
      </div>

      <div className="cam-layout">
        <nav className="cam-nav">
          {sections.map((s) => (
            <button
              key={s.id}
              type="button"
              className={`cam-nav-item${selected && selected.id === s.id ? ' active' : ''}`}
              onClick={() => setSelectedId(s.id)}
            >
              <span className="cam-nav-order">{s.order}</span>
              <span className="cam-nav-name">{s.name}</span>
              {s.fixed_format ? (
                <span className="cam-nav-icon" title="Fixed format — output preferences not applied">
                  🔒
                </span>
              ) : null}
              {s.current_version_no > 1 ? (
                <span className="cam-nav-icon edited-dot" title={`Edited — version ${s.current_version_no}`}>
                  ●
                </span>
              ) : null}
            </button>
          ))}
        </nav>

        <div className="cam-center">
          {selected ? (
            <SectionView
              key={selected.id}
              cam={cam}
              section={selected}
              editable={isDraft}
              onSaved={patchSection}
              onReload={reloadCam}
            />
          ) : (
            <EmptyState title="This CAM has no sections" />
          )}
        </div>

        <aside className="cam-chat">
          <ChatPanel cam={cam} activeSection={selected} enabled={isDraft} onCamReload={reloadCam} />
        </aside>
      </div>

      {confirmFinalise ? (
        <ConfirmDialog
          title="Finalise CAM"
          message="Finalising locks the AI-assisted draft state and removes the draft watermark from exports. Continue?"
          confirmLabel="Finalise"
          busy={finalising}
          onConfirm={finalise}
          onCancel={() => setConfirmFinalise(false)}
        />
      ) : null}
    </div>
  );
}
