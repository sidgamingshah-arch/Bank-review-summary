import { useCallback, useEffect, useRef, useState } from 'react';
import { api, ApiError, errorMessage } from '../../api/client';
import type { Cam, CamSection, Run, SectionVersion } from '../../api/types';
import { Markdown } from '../../components/Markdown';
import { Modal } from '../../components/Modal';
import { Spinner } from '../../components/Spinner';
import { useToast } from '../../components/Toast';
import { HistoryDrawer } from './HistoryDrawer';

const AUTOSAVE_MS = 1200;
const REGEN_POLL_MS = 1500;
const REGEN_MAX_POLLS = 80;

interface Props {
  cam: Cam;
  section: CamSection;
  editable: boolean;
  onSaved: (sectionId: string, content: string, versionNo: number) => void;
  onReload: () => Promise<Cam | null>;
}

export function SectionView({ cam, section, editable, onSaved, onReload }: Props) {
  const toast = useToast();
  const isGapTrailer = section.section_code === '_gaps';

  const [editing, setEditing] = useState(false);
  const [content, setContent] = useState(section.content);
  const [baseVersion, setBaseVersion] = useState(section.current_version_no);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [conflict, setConflict] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [namePrompt, setNamePrompt] = useState(false);
  const [versionName, setVersionName] = useState('');
  const [regenerating, setRegenerating] = useState(false);
  const contentRef = useRef(section.content);

  // Section content may change under us (regeneration, suggestion accepted) while not editing.
  useEffect(() => {
    if (!editing) {
      contentRef.current = section.content;
      setContent(section.content);
      setBaseVersion(section.current_version_no);
      setDirty(false);
      setConflict(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [section.content, section.current_version_no]);

  const save = useCallback(
    async (name?: string) => {
      setSaving(true);
      try {
        const body: Record<string, unknown> = { content, base_version_no: baseVersion };
        if (name) body.version_name = name;
        const v = await api.put<SectionVersion>(`/api/cams/${cam.id}/sections/${section.id}`, body);
        setBaseVersion(v.version_no);
        // Keystrokes that landed while the save was in flight stay dirty.
        setDirty(contentRef.current !== content);
        onSaved(section.id, content, v.version_no);
        if (name) toast.success(`Version "${name}" saved`);
        return true;
      } catch (err) {
        if (err instanceof ApiError && err.status === 409) {
          setConflict(true);
          toast.error('Section changed elsewhere — reload to continue editing');
        } else {
          toast.error(errorMessage(err));
        }
        return false;
      } finally {
        setSaving(false);
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [content, baseVersion, cam.id, section.id],
  );

  const saveRef = useRef(save);
  saveRef.current = save;

  // Debounced autosave: 1.2s after typing stops.
  useEffect(() => {
    if (!editing || !dirty || conflict) return;
    const t = window.setTimeout(() => {
      saveRef.current();
    }, AUTOSAVE_MS);
    return () => window.clearTimeout(t);
  }, [content, editing, dirty, conflict]);

  const reloadAfterConflict = async () => {
    const fresh = await onReload();
    if (fresh) {
      const freshSection = fresh.sections.find((s) => s.id === section.id);
      if (freshSection) {
        contentRef.current = freshSection.content;
        setContent(freshSection.content);
        setBaseVersion(freshSection.current_version_no);
      }
    }
    setConflict(false);
    setDirty(false);
    toast.info('Section reloaded with the latest content');
  };

  const regenerate = async () => {
    setRegenerating(true);
    try {
      await api.post(`/api/runs/${cam.run_id}/sections/${encodeURIComponent(section.section_code)}/regenerate`);
      toast.info(`Regenerating "${section.name}"…`);
      // Briefly poll the run until this section settles, then reload the CAM.
      for (let i = 0; i < REGEN_MAX_POLLS; i++) {
        await new Promise((resolve) => window.setTimeout(resolve, REGEN_POLL_MS));
        const run = await api.get<Run>(`/api/runs/${cam.run_id}`);
        const s = run.sections.find((x) => x.section_code === section.section_code);
        if (!s || s.status === 'complete') {
          await onReload();
          toast.success(`Section "${section.name}" regenerated (new version, source: regeneration)`);
          setRegenerating(false);
          return;
        }
        if (s.status === 'failed') {
          toast.error(`Regeneration failed: ${s.error ?? 'unknown error'}`);
          setRegenerating(false);
          return;
        }
      }
      toast.error('Regeneration is taking longer than expected — check the run page');
    } catch (err) {
      toast.error(errorMessage(err));
    } finally {
      setRegenerating(false);
    }
  };

  const startEdit = () => {
    contentRef.current = section.content;
    setContent(section.content);
    setBaseVersion(section.current_version_no);
    setDirty(false);
    setConflict(false);
    setEditing(true);
  };

  const stopEdit = async () => {
    if (dirty && !conflict) {
      await saveRef.current();
    }
    setEditing(false);
  };

  return (
    <div className="section-panel">
      <div className="section-head">
        <div className="section-title">
          <h2>
            {section.order}. {section.name}
          </h2>
          <span className="muted mono">{section.section_code}</span>
          {section.fixed_format ? (
            <span className="chip chip-gray" title="Fixed format — output preferences not applied">
              🔒 fixed format
            </span>
          ) : null}
          <span className="muted">v{baseVersion}</span>
          {saving ? <Spinner small label="Saving…" /> : dirty ? <span className="muted">unsaved…</span> : null}
        </div>
        <div className="btn-row">
          {editable && !isGapTrailer ? (
            editing ? (
              <>
                <button type="button" className="btn btn-sm" onClick={() => setNamePrompt(true)} disabled={conflict}>
                  Save version…
                </button>
                <button type="button" className="btn btn-sm btn-primary" onClick={stopEdit}>
                  Done
                </button>
              </>
            ) : (
              <>
                <button type="button" className="btn btn-sm" onClick={startEdit}>
                  Edit
                </button>
                <button type="button" className="btn btn-sm" onClick={regenerate} disabled={regenerating}>
                  {regenerating ? 'Regenerating…' : 'Regenerate section'}
                </button>
              </>
            )
          ) : null}
          <button type="button" className="btn btn-sm" onClick={() => setHistoryOpen(true)}>
            History
          </button>
        </div>
      </div>

      {isGapTrailer ? (
        <div className="banner banner-info slim">Data gaps disclosed for this generation — this section is read-only.</div>
      ) : null}

      {conflict ? (
        <div className="banner banner-error slim">
          Section changed elsewhere — your edits cannot be saved onto version {baseVersion}.
          <button type="button" className="btn btn-sm" onClick={reloadAfterConflict}>
            Reload latest
          </button>
        </div>
      ) : null}

      {editing && !isGapTrailer ? (
        <textarea
          className="textarea section-editor"
          value={content}
          onChange={(e) => {
            contentRef.current = e.target.value;
            setContent(e.target.value);
            setDirty(true);
          }}
          spellCheck={false}
        />
      ) : (
        <div className="section-content">
          <Markdown content={section.content} />
        </div>
      )}

      {historyOpen ? (
        <HistoryDrawer camId={cam.id} section={section} onClose={() => setHistoryOpen(false)} />
      ) : null}

      {namePrompt ? (
        <Modal
          title="Save named version"
          onClose={() => setNamePrompt(false)}
          footer={
            <>
              <button type="button" className="btn" onClick={() => setNamePrompt(false)}>
                Cancel
              </button>
              <button
                type="button"
                className="btn btn-primary"
                disabled={!versionName.trim() || saving}
                onClick={async () => {
                  const ok = await save(versionName.trim());
                  if (ok) {
                    setNamePrompt(false);
                    setVersionName('');
                  }
                }}
              >
                Save version
              </button>
            </>
          }
        >
          <div className="field">
            <label>Version name</label>
            <input
              className="input"
              value={versionName}
              placeholder="e.g. post-credit-committee edits"
              onChange={(e) => setVersionName(e.target.value)}
              autoFocus
            />
          </div>
        </Modal>
      ) : null}
    </div>
  );
}
