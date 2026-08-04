import { useCallback, useEffect, useMemo, useState } from 'react';
import { FlaskConical, PencilLine } from 'lucide-react';
import { api, errorMessage } from '../../api/client';
import type { ItemDetail, ItemSummary, PromptPayload, Version, VersionMeta } from '../../api/types';
import { Modal } from '../../components/Modal';
import { Spinner } from '../../components/Spinner';
import { StatusChip } from '../../components/StatusChip';
import { useToast } from '../../components/Toast';
import { VersionEditorModal } from './VersionEditorModal';
import { SandboxModal } from './SandboxModal';

// keys the backend reserves for global-scope prompts (is_section_prompt === false)
const GLOBAL_KEYS = new Set([
  'global_standing_rules',
  'agent_extraction_rules',
  'agent_summarisation_rules',
  'agent_materiality_rules',
  'agent_consistency_rules',
]);
const isSectionPrompt = (key: string) => !GLOBAL_KEYS.has(key);

const LIFECYCLE = ['draft', 'in_review', 'published'] as const;

interface Props {
  items: ItemSummary[];
  selectedKey: string | null;
  onSelectKey: (key: string) => void;
  onChanged: () => void;
  onNew: () => void;
}

/** Editor view of the prompt master: grouped prompt list · prompt-as-document
 *  · maker-checker governance rail. Mutations reuse the same endpoints as the
 *  Workspace view (submit/approve/reject/rollback) and the shared editors. */
export function PromptEditor({ items, selectedKey, onSelectKey, onChanged, onNew }: Props) {
  const toast = useToast();
  const [filter, setFilter] = useState('');
  const [detail, setDetail] = useState<ItemDetail | null>(null);
  const [payload, setPayload] = useState<PromptPayload | null>(null);
  const [shownVersion, setShownVersion] = useState<number | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [editBase, setEditBase] = useState<Version | null>(null);
  const [sandbox, setSandbox] = useState(false);
  const [rejecting, setRejecting] = useState<VersionMeta | null>(null);
  const [rejectReason, setRejectReason] = useState('');
  const [viewJson, setViewJson] = useState<Version | null>(null);

  const load = useCallback(async () => {
    if (!selectedKey) {
      setDetail(null);
      setPayload(null);
      return;
    }
    try {
      const d = await api.get<ItemDetail>(`/api/masters/prompts/${encodeURIComponent(selectedKey)}`);
      setDetail(d);
      const show = d.published_version ?? Math.max(...d.versions.map((v) => v.version_no));
      setShownVersion(show);
      const v = await api.get<Version>(`/api/masters/prompts/${encodeURIComponent(selectedKey)}/versions/${show}`);
      setPayload(v.payload as PromptPayload);
    } catch (err) {
      toast.error(errorMessage(err));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedKey]);

  useEffect(() => {
    setDetail(null);
    setPayload(null);
    load();
  }, [load]);

  const refresh = async () => {
    await load();
    onChanged();
  };

  const lifecycle = async (versionNo: number, action: 'submit' | 'approve' | 'rollback') => {
    setBusy(`${action}-${versionNo}`);
    try {
      await api.post(`/api/masters/prompts/${encodeURIComponent(selectedKey!)}/versions/${versionNo}/${action}`);
      const labels = { submit: 'submitted for review', approve: 'approved & published', rollback: 'cloned into a draft' };
      toast.success(`v${versionNo} ${labels[action]}`);
      await refresh();
    } catch (err) {
      toast.error(errorMessage(err));
    } finally {
      setBusy(null);
    }
  };

  const reject = async () => {
    if (!rejecting) return;
    setBusy(`reject-${rejecting.version_no}`);
    try {
      await api.post(`/api/masters/prompts/${encodeURIComponent(selectedKey!)}/versions/${rejecting.version_no}/reject`, {
        reason: rejectReason.trim(),
      });
      toast.success(`v${rejecting.version_no} rejected`);
      setRejecting(null);
      setRejectReason('');
      await refresh();
    } catch (err) {
      toast.error(errorMessage(err));
    } finally {
      setBusy(null);
    }
  };

  const openView = async (versionNo: number) => {
    try {
      setViewJson(await api.get<Version>(`/api/masters/prompts/${encodeURIComponent(selectedKey!)}/versions/${versionNo}`));
    } catch (err) {
      toast.error(errorMessage(err));
    }
  };

  const editAsNewVersion = async () => {
    if (!detail) return;
    const latest = Math.max(...detail.versions.map((v) => v.version_no));
    try {
      setEditBase(await api.get<Version>(`/api/masters/prompts/${encodeURIComponent(selectedKey!)}/versions/${latest}`));
    } catch (err) {
      toast.error(errorMessage(err));
    }
  };

  const groups = useMemo(() => {
    const q = filter.trim().toLowerCase();
    const match = (it: ItemSummary) => !q || it.key.toLowerCase().includes(q);
    const section = items.filter((i) => isSectionPrompt(i.key) && match(i));
    const global = items.filter((i) => !isSectionPrompt(i.key) && match(i));
    return [
      { label: 'Section prompts', items: section },
      { label: 'Standing & agent rules', items: global },
    ].filter((g) => g.items.length > 0);
  }, [items, filter]);

  const placeholders = useMemo(() => {
    if (!payload?.prompt_text) return [];
    const found = payload.prompt_text.match(/\{\{\s*[\w.]+\s*\}\}/g) ?? [];
    return Array.from(new Set(found.map((m) => m.replace(/[{}\s]/g, ''))));
  }, [payload]);

  const scrollTo = (id: string) => document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' });

  const versions = detail ? [...detail.versions].sort((a, b) => b.version_no - a.version_no) : [];
  const currentStatus = detail?.published_version != null ? 'published' : versions[0]?.status ?? 'draft';

  return (
    <div className="pm-grid">
      {/* left — grouped, searchable prompt list */}
      <nav className="pm-list">
        <input
          className="input slim"
          style={{ width: '100%' }}
          placeholder="Filter prompts…"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
        />
        <button type="button" className="btn btn-sm btn-primary btn-block" onClick={onNew}>
          New prompt
        </button>
        {groups.map((g) => (
          <div key={g.label} className="pm-group">
            <div className="pm-group-label">{g.label}</div>
            {g.items.map((it) => (
              <button
                key={it.key}
                type="button"
                className={`pm-item${selectedKey === it.key ? ' active' : ''}`}
                onClick={() => onSelectKey(it.key)}
              >
                <span className={`pm-dot ${it.published_version != null ? 'ok' : 'warn'}`} />
                <span className="pm-item-key mono">{it.key}</span>
              </button>
            ))}
          </div>
        ))}
      </nav>

      {/* center — prompt as a document */}
      <div className="pm-doc">
        {!selectedKey ? (
          <div className="cam-outline-empty">Select a prompt to view it as a document.</div>
        ) : !payload ? (
          <Spinner label="Loading prompt…" />
        ) : (
          <>
            <div className="pm-doc-head">
              <div className="pm-doc-title">
                <h2>{payload.section_name}</h2>
                <span className="mono muted">{payload.section_code}</span>
                <span className={`chip ${payload.scope === 'global' ? 'chip-purple' : 'chip-blue'}`}>{payload.scope}</span>
                <span className="chip mono">
                  {detail?.published_version != null ? `pub v${detail.published_version}` : `v${shownVersion}`}
                </span>
              </div>
              <div className="section-actions">
                <select
                  className="select slim section-jump"
                  value={selectedKey}
                  onChange={(e) => onSelectKey(e.target.value)}
                  aria-label="Jump to prompt"
                >
                  {groups.map((g) => (
                    <optgroup key={g.label} label={g.label}>
                      {g.items.map((it) => (
                        <option key={it.key} value={it.key}>
                          {it.key}
                        </option>
                      ))}
                    </optgroup>
                  ))}
                </select>
                <button type="button" className="btn btn-sm btn-primary" onClick={editAsNewVersion}>
                  <PencilLine size={14} strokeWidth={1.8} /> Edit as new version
                </button>
              </div>
            </div>

            <div className="pm-parts">
              <button type="button" className="pm-part" onClick={() => scrollTo('pm-instruction')}>
                Instruction
              </button>
              <button type="button" className="pm-part" onClick={() => scrollTo('pm-variables')}>
                Variables
              </button>
              <button type="button" className="pm-part" onClick={() => scrollTo('pm-grounding')}>
                Grounding
              </button>
              <button type="button" className="pm-part" onClick={() => scrollTo('pm-meta')}>
                Meta
              </button>
            </div>

            <section id="pm-instruction" className="pm-section">
              <h3>Instruction</h3>
              <pre className="pm-prompt-text">{payload.prompt_text}</pre>
            </section>

            <section id="pm-variables" className="pm-section">
              <h3>Variables</h3>
              {placeholders.length === 0 ? (
                <p className="muted">No template placeholders.</p>
              ) : (
                <div className="chip-row">
                  {placeholders.map((p) => (
                    <span key={p} className="chip-token">{`{{${p}}}`}</span>
                  ))}
                </div>
              )}
            </section>

            <section id="pm-grounding" className="pm-section">
              <h3>Grounding</h3>
              <div className="chip-row">
                <span className={`chip ${payload.uses_industry_kpis ? 'chip-green' : 'chip-gray'}`}>
                  industry KPIs: {payload.uses_industry_kpis ? 'on' : 'off'}
                </span>
                <span className={`chip ${payload.uses_external_context ? 'chip-green' : 'chip-gray'}`}>
                  external context: {payload.uses_external_context ? 'on' : 'off'}
                </span>
                {payload.source_doc_types.map((d) => (
                  <span key={d} className="chip chip-blue mono">{d}</span>
                ))}
              </div>
              {payload.rendering_hints ? <p className="muted">{payload.rendering_hints}</p> : null}
            </section>

            <section id="pm-meta" className="pm-section">
              <h3>Output &amp; model</h3>
              <dl className="kv">
                <div className="kv-pair">
                  <dt>Scope</dt>
                  <dd>{payload.scope}</dd>
                </div>
                {payload.model_overrides?.model ? (
                  <div className="kv-pair">
                    <dt>Model override</dt>
                    <dd className="mono">{payload.model_overrides.model}</dd>
                  </div>
                ) : null}
                {payload.model_overrides?.temperature != null ? (
                  <div className="kv-pair">
                    <dt>Temperature</dt>
                    <dd className="mono">{payload.model_overrides.temperature}</dd>
                  </div>
                ) : null}
              </dl>
            </section>
          </>
        )}
      </div>

      {/* right — maker-checker governance rail */}
      <aside className="pm-gov">
        {!detail ? (
          selectedKey ? <Spinner label="Loading…" /> : <div className="cam-outline-empty">No prompt selected.</div>
        ) : (
          <>
            <div className="pm-gov-head">Governance</div>
            <div className="pm-stepper">
              {LIFECYCLE.map((stage) => {
                const reached =
                  LIFECYCLE.indexOf(stage) <= LIFECYCLE.indexOf(currentStatus as (typeof LIFECYCLE)[number]);
                return (
                  <div key={stage} className={`pm-step${reached ? ' reached' : ''}${stage === currentStatus ? ' current' : ''}`}>
                    <span className="pm-step-dot" />
                    {stage.replace('_', ' ')}
                  </div>
                );
              })}
            </div>

            <button type="button" className="btn btn-sm btn-block" onClick={() => setSandbox(true)}>
              <FlaskConical size={14} strokeWidth={1.8} /> Sandbox test
            </button>

            <div className="pm-gov-sub">Version history</div>
            <div className="pm-versions">
              {versions.map((v) => (
                <div key={v.version_no} className="pm-version">
                  <div className="pm-version-top">
                    <span className="mono">v{v.version_no}</span>
                    <StatusChip status={v.status} />
                  </div>
                  <div className="pm-version-meta muted">
                    maker {v.created_by}
                    {v.approved_by ? ` · approver ${v.approved_by}` : ''}
                  </div>
                  <div className="btn-row">
                    <button type="button" className="btn btn-sm" onClick={() => openView(v.version_no)} title="View payload">
                      View
                    </button>
                    {v.status === 'draft' ? (
                      <button type="button" className="btn btn-sm" disabled={busy !== null} onClick={() => lifecycle(v.version_no, 'submit')}>
                        Submit
                      </button>
                    ) : null}
                    {v.status === 'in_review' ? (
                      <>
                        <button
                          type="button"
                          className="btn btn-sm btn-primary"
                          disabled={busy !== null}
                          onClick={() => lifecycle(v.version_no, 'approve')}
                          title="Approver must differ from maker"
                        >
                          Approve
                        </button>
                        <button type="button" className="btn btn-sm btn-danger" disabled={busy !== null} onClick={() => setRejecting(v)}>
                          Reject
                        </button>
                      </>
                    ) : null}
                    <button type="button" className="btn btn-sm" disabled={busy !== null} onClick={() => lifecycle(v.version_no, 'rollback')} title="Clone into a new draft">
                      Rollback
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </>
        )}
      </aside>

      {editBase && selectedKey ? (
        <VersionEditorModal
          mtype="prompts"
          mode="newVersion"
          itemKey={selectedKey}
          initialPayload={editBase.payload}
          onClose={() => setEditBase(null)}
          onSaved={() => {
            setEditBase(null);
            refresh();
          }}
        />
      ) : null}

      {sandbox && selectedKey ? <SandboxModal promptKey={selectedKey} onClose={() => setSandbox(false)} /> : null}

      {rejecting ? (
        <Modal
          title={`Reject v${rejecting.version_no}`}
          onClose={() => setRejecting(null)}
          footer={
            <>
              <button type="button" className="btn" onClick={() => setRejecting(null)}>
                Cancel
              </button>
              <button type="button" className="btn btn-danger" disabled={!rejectReason.trim() || busy !== null} onClick={reject}>
                Reject version
              </button>
            </>
          }
        >
          <div className="field">
            <label>Reason</label>
            <textarea className="textarea" value={rejectReason} onChange={(e) => setRejectReason(e.target.value)} autoFocus />
          </div>
        </Modal>
      ) : null}

      {viewJson ? (
        <Modal title={`${selectedKey} — v${viewJson.version_no}`} onClose={() => setViewJson(null)} wide>
          <div className="chip-row head-chips">
            <StatusChip status={viewJson.status} />
            <span className="muted">maker {viewJson.created_by}</span>
          </div>
          <p className="change-note-view">“{viewJson.change_note}”</p>
          <pre className="detail-json">{JSON.stringify(viewJson.payload, null, 2)}</pre>
        </Modal>
      ) : null}
    </div>
  );
}
