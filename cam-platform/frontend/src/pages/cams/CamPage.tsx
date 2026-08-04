import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { ChevronRight, Download, Lock, ShieldCheck } from 'lucide-react';
import { api, errorMessage } from '../../api/client';
import type { Cam, CamSection } from '../../api/types';
import { ConfirmDialog } from '../../components/ConfirmDialog';
import { EmptyState } from '../../components/EmptyState';
import { PageLoading } from '../../components/Spinner';
import { useToast } from '../../components/Toast';
import { SectionView } from './SectionView';
import { ChatPanel } from './ChatPanel';
import { CHAPTER_ORDER, chapterKey } from './chapters';

export function CamPage() {
  const { camId = '' } = useParams();
  const toast = useToast();
  const [cam, setCam] = useState<Cam | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [filter, setFilter] = useState('');
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

  const sections = useMemo(
    () => (cam ? [...cam.sections].sort((a, b) => a.order - b.order) : []),
    [cam],
  );
  const selected = sections.find((s) => s.id === selectedId) ?? sections[0] ?? null;

  // Keep the active section's chapter expanded (spec: only the active chapter
  // is open by default; navigating reveals the target's chapter).
  useEffect(() => {
    if (!selected) return;
    const k = chapterKey(selected);
    setExpanded((prev) => (prev.has(k) ? prev : new Set(prev).add(k)));
  }, [selectedId, selected]);

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

  const isDraft = cam.status === 'draft';
  const q = filter.trim().toLowerCase();
  const matches = (s: CamSection) =>
    !q || s.name.toLowerCase().includes(q) || s.section_code.toLowerCase().includes(q);

  const chapters = CHAPTER_ORDER.map((ch) => ({
    ...ch,
    items: sections.filter((s) => chapterKey(s) === ch.key && matches(s)),
  })).filter((ch) => ch.items.length > 0);

  const toggleChapter = (key: string) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      next.has(key) ? next.delete(key) : next.add(key);
      return next;
    });

  const renderRow = (s: CamSection) => (
    <button
      key={s.id}
      type="button"
      className={`cam-sec${selected && selected.id === s.id ? ' active' : ''}`}
      onClick={() => setSelectedId(s.id)}
    >
      <span className="cam-sec-order">{s.order}</span>
      <span className="cam-sec-name">{s.name}</span>
      {s.fixed_format ? (
        <span className="cam-sec-lock" title="Fixed format — output preferences not applied">
          <Lock size={12} strokeWidth={2} />
        </span>
      ) : null}
      {s.current_version_no > 1 ? (
        <span className="cam-sec-dot" title={`Edited — version ${s.current_version_no}`} />
      ) : null}
    </button>
  );

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
          <span className="chip chip-green cam-check" title="Every figure was checked against a source during generation">
            <ShieldCheck size={13} strokeWidth={2} /> Figures source-checked
          </span>
          {isDraft ? (
            <span className="cam-badge cam-badge-draft">AI-assisted draft</span>
          ) : (
            <span className="cam-badge cam-badge-final">Final</span>
          )}
          {isDraft ? (
            <button type="button" className="btn btn-primary" onClick={() => setConfirmFinalise(true)}>
              Finalise
            </button>
          ) : null}
          <button type="button" className="btn" disabled={exporting === 'docx'} onClick={() => exportCam('docx')}>
            <Download size={15} strokeWidth={1.8} /> {exporting === 'docx' ? 'Exporting…' : 'DOCX'}
          </button>
          <button type="button" className="btn" disabled={exporting === 'pdf'} onClick={() => exportCam('pdf')}>
            <Download size={15} strokeWidth={1.8} /> {exporting === 'pdf' ? 'Exporting…' : 'PDF'}
          </button>
        </div>
      </div>

      <div className="cam-grid">
        <nav className="cam-outline">
          <input
            className="input slim cam-filter"
            style={{ width: '100%' }}
            placeholder="Filter sections…"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          />
          {chapters.length === 0 ? (
            <div className="cam-outline-empty">No sections match “{filter}”.</div>
          ) : q ? (
            // filtering flattens to matches
            chapters.flatMap((ch) => ch.items).map(renderRow)
          ) : (
            chapters.map((ch) => {
              const open = expanded.has(ch.key);
              return (
                <div key={ch.key} className="cam-chapter">
                  <button type="button" className="cam-chapter-head" onClick={() => toggleChapter(ch.key)}>
                    <span className={`cam-chapter-chevron${open ? ' open' : ''}`}>
                      <ChevronRight size={13} strokeWidth={2.2} />
                    </span>
                    {ch.label}
                    <span className="cam-chapter-count">{ch.items.length}</span>
                  </button>
                  {open ? ch.items.map(renderRow) : null}
                </div>
              );
            })
          )}
        </nav>

        <div className="cam-doc">
          {selected ? (
            <SectionView
              key={selected.id}
              cam={cam}
              section={selected}
              sections={sections}
              editable={isDraft}
              onSaved={patchSection}
              onReload={reloadCam}
              onSelectSection={setSelectedId}
            />
          ) : (
            <EmptyState title="This CAM has no sections" />
          )}
        </div>

        <aside className="cam-assistant">
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
