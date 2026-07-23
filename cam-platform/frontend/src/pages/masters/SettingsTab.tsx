import { useEffect, useState } from 'react';
import { api, errorMessage } from '../../api/client';
import type { LlmInfo, MasterSettings } from '../../api/types';
import { PageLoading } from '../../components/Spinner';
import { useToast } from '../../components/Toast';

interface Editable {
  tagging_confidence_threshold: string;
  tagging_mode: 'ai_first' | 'keyword_first' | 'keyword_only';
  agents_materiality_enabled: boolean;
  agents_consistency_enabled: boolean;
  agent_revision_limit: string;
  connectors_news_enabled: boolean;
  connectors_search_enabled: boolean;
}

function toForm(s: MasterSettings): Editable {
  return {
    tagging_confidence_threshold: String(s.tagging_confidence_threshold ?? 0.55),
    tagging_mode: (s.tagging_mode as Editable['tagging_mode']) ?? 'ai_first',
    agents_materiality_enabled: s.agents_materiality_enabled ?? true,
    agents_consistency_enabled: s.agents_consistency_enabled ?? true,
    agent_revision_limit: String(s.agent_revision_limit ?? 1),
    connectors_news_enabled: s.connectors_news_enabled ?? false,
    connectors_search_enabled: s.connectors_search_enabled ?? false,
  };
}

export function SettingsTab() {
  const toast = useToast();
  const [llm, setLlm] = useState<LlmInfo | null>(null);
  const [form, setForm] = useState<Editable | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api
      .get<MasterSettings>('/api/masters/settings')
      .then((s) => {
        if (cancelled) return;
        setForm(toForm(s));
        setLlm(s._llm ?? null);
      })
      .catch((err) => toast.error(errorMessage(err)));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const set = <K extends keyof Editable>(key: K, value: Editable[K]) =>
    setForm((f) => (f ? { ...f, [key]: value } : f));

  const save = async () => {
    if (!form) return;
    if (form.tagging_confidence_threshold.trim() === '' || form.agent_revision_limit.trim() === '') {
      toast.error('Confidence threshold and revision limit are required');
      return;
    }
    const threshold = Number(form.tagging_confidence_threshold);
    const revisionLimit = Number(form.agent_revision_limit);
    if (Number.isNaN(threshold) || threshold < 0 || threshold > 1) {
      toast.error('Tagging confidence threshold must be between 0 and 1');
      return;
    }
    if (!Number.isInteger(revisionLimit) || revisionLimit < 0 || revisionLimit > 3) {
      toast.error('Agent revision limit must be an integer between 0 and 3');
      return;
    }
    setSaving(true);
    try {
      const updated = await api.put<MasterSettings>('/api/masters/settings', {
        tagging_confidence_threshold: threshold,
        tagging_mode: form.tagging_mode,
        agents_materiality_enabled: form.agents_materiality_enabled,
        agents_consistency_enabled: form.agents_consistency_enabled,
        agent_revision_limit: revisionLimit,
        connectors_news_enabled: form.connectors_news_enabled,
        connectors_search_enabled: form.connectors_search_enabled,
      });
      setForm(toForm(updated));
      setLlm(updated._llm ?? llm);
      toast.success('Settings saved');
    } catch (err) {
      toast.error(errorMessage(err));
    } finally {
      setSaving(false);
    }
  };

  if (!form) return <PageLoading label="Loading settings…" />;

  return (
    <div className="settings-stack">
      <div className="card settings-card">
        <div className="card-head">
          <h2>Platform settings</h2>
        </div>

        <h3 className="settings-group">Document tagging</h3>
        <div className="form-grid-2">
          <div className="field">
            <label>Confidence threshold</label>
            <input
              className="input slim"
              type="number"
              min={0}
              max={1}
              step={0.05}
              value={form.tagging_confidence_threshold}
              onChange={(e) => set('tagging_confidence_threshold', e.target.value)}
            />
            <div className="hint">Auto-tags below this confidence are flagged “needs review” (0–1).</div>
          </div>
          <div className="field">
            <label>Tagging mode</label>
            <select
              className="select slim"
              value={form.tagging_mode}
              onChange={(e) => set('tagging_mode', e.target.value as Editable['tagging_mode'])}
            >
              <option value="ai_first">AI first (model classifies, keywords corroborate)</option>
              <option value="keyword_first">Keyword first (model only when keywords are weak)</option>
              <option value="keyword_only">Keyword only (never call the model)</option>
            </select>
          </div>
        </div>

        <h3 className="settings-group">Assurance agents</h3>
        <div className="check-row">
          <label className="check-pill">
            <input
              type="checkbox"
              checked={form.agents_materiality_enabled}
              onChange={(e) => set('agents_materiality_enabled', e.target.checked)}
            />
            Materiality check agent
          </label>
          <label className="check-pill">
            <input
              type="checkbox"
              checked={form.agents_consistency_enabled}
              onChange={(e) => set('agents_consistency_enabled', e.target.checked)}
            />
            Consistency check agent
          </label>
        </div>
        <div className="field">
          <label>Revision limit</label>
          <input
            className="input slim"
            type="number"
            min={0}
            max={3}
            step={1}
            value={form.agent_revision_limit}
            onChange={(e) => set('agent_revision_limit', e.target.value)}
          />
          <div className="hint">How many times a failed gate may send a section back to be redrafted (0–3).</div>
        </div>

        <h3 className="settings-group">External connectors</h3>
        <div className="check-row">
          <label className="check-pill">
            <input
              type="checkbox"
              checked={form.connectors_news_enabled}
              onChange={(e) => set('connectors_news_enabled', e.target.checked)}
            />
            Negative-news connector
          </label>
          <label className="check-pill">
            <input
              type="checkbox"
              checked={form.connectors_search_enabled}
              onChange={(e) => set('connectors_search_enabled', e.target.checked)}
            />
            Web / search connector
          </label>
        </div>
        <div className="hint">
          When on, sections that opt in (via their prompt) are enriched with the client-provided
          feed as additional, source-labelled grounding. The endpoint URL is set at deployment; with
          no URL configured a clearly-marked mock feed is used. Off = document-only generation.
        </div>

        <div className="actions-row">
          <button type="button" className="btn btn-primary" disabled={saving} onClick={save}>
            {saving ? 'Saving…' : 'Save settings'}
          </button>
        </div>
      </div>

      {llm ? (
        <div className="card settings-card">
          <div className="card-head">
            <h2>LLM endpoint</h2>
            <span className="chip chip-gray">read-only · set via environment</span>
          </div>
          <dl className="kv">
            <dt>Provider</dt>
            <dd className="mono">{llm.provider}</dd>
            <dt>Model</dt>
            <dd className="mono">{llm.model}</dd>
            <dt>Base URL</dt>
            <dd className="mono">{llm.base_url ?? '— (SDK default)'}</dd>
            <dt>Max tokens</dt>
            <dd className="mono">{llm.max_tokens}</dd>
            <dt>API key</dt>
            <dd>
              {llm.api_key_configured ? (
                <span className="chip chip-green">configured</span>
              ) : (
                <span className="chip chip-amber">not set</span>
              )}{' '}
              <span className="muted mono">({llm.api_key_env})</span>
            </dd>
          </dl>
          <div className="hint">
            The provider, endpoint and model are configured through environment variables
            (CAM_LLM_PROVIDER, CAM_GENAI_BASE_URL, CAM_GENAI_MODEL) and read at service start — the
            API key value is never exposed here. Changing them requires a service restart.
          </div>
        </div>
      ) : null}
    </div>
  );
}
