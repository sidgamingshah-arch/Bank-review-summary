import { useEffect, useState } from 'react';
import { api, errorMessage } from '../../api/client';
import type { LlmConfigInput, LlmInfo, MasterSettings } from '../../api/types';
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
  rag_mode: 'off' | 'keyword' | 'embedding';
  rag_top_k: string;
}

interface LlmForm {
  provider: string;
  model: string;
  base_url: string;
  temperature: string;
  max_tokens: string;
  timeout_seconds: string;
  auth_scheme: string;
  api_key_env: string;
  embed_provider: string;
  embed_model: string;
  embed_base_url: string;
  embed_api_key_env: string;
  azure_endpoint: string;
  azure_api_version: string;
  azure_reasoning: boolean;
  azure_api_key_env: string;
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
    rag_mode: (s.rag_mode as Editable['rag_mode']) ?? (s.rag_enabled ? 'embedding' : 'off'),
    rag_top_k: String(s.rag_top_k ?? 6),
  };
}

function toLlmForm(l: LlmInfo): LlmForm {
  return {
    provider: l.provider ?? 'mock',
    model: l.model ?? '',
    base_url: l.base_url ?? '',
    temperature: String(l.temperature ?? 0),
    max_tokens: String(l.max_tokens ?? 2000),
    timeout_seconds: String(l.timeout_seconds ?? 120),
    auth_scheme: l.auth_scheme ?? 'Bearer',
    api_key_env: l.api_key_env ?? 'CAM_GENAI_API_KEY',
    embed_provider: l.embed_provider ?? 'mock',
    embed_model: l.embed_model ?? '',
    embed_base_url: l.embed_base_url ?? '',
    embed_api_key_env: l.embed_api_key_env ?? 'CAM_GENAI_API_KEY',
    azure_endpoint: l.azure_endpoint ?? '',
    azure_api_version: l.azure_api_version ?? '2024-10-21',
    azure_reasoning: l.azure_reasoning ?? false,
    azure_api_key_env: l.azure_api_key_env ?? 'AZURE_OPENAI_API_KEY',
  };
}

export function SettingsTab() {
  const toast = useToast();
  const [form, setForm] = useState<Editable | null>(null);
  const [llm, setLlm] = useState<LlmForm | null>(null);
  const [keyConfigured, setKeyConfigured] = useState(false);
  const [embedKeyConfigured, setEmbedKeyConfigured] = useState(false);
  const [azureKeyConfigured, setAzureKeyConfigured] = useState(false);
  const [saving, setSaving] = useState(false);
  const [savingLlm, setSavingLlm] = useState(false);

  const ingest = (s: MasterSettings) => {
    setForm(toForm(s));
    if (s._llm) {
      setLlm(toLlmForm(s._llm));
      setKeyConfigured(Boolean(s._llm.api_key_configured));
      setEmbedKeyConfigured(Boolean(s._llm.embed_api_key_configured));
      setAzureKeyConfigured(Boolean(s._llm.azure_api_key_configured));
    }
  };

  useEffect(() => {
    let cancelled = false;
    api
      .get<MasterSettings>('/api/masters/settings')
      .then((s) => {
        if (!cancelled) ingest(s);
      })
      .catch((err) => toast.error(errorMessage(err)));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const set = <K extends keyof Editable>(key: K, value: Editable[K]) =>
    setForm((f) => (f ? { ...f, [key]: value } : f));
  const setL = <K extends keyof LlmForm>(key: K, value: LlmForm[K]) =>
    setLlm((f) => (f ? { ...f, [key]: value } : f));

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
    if (form.rag_top_k.trim() === '') {
      toast.error('Retrieval passages (top-K) is required');
      return;
    }
    const ragTopK = Number(form.rag_top_k);
    if (!Number.isInteger(ragTopK) || ragTopK < 1 || ragTopK > 50) {
      toast.error('Retrieval passages (top-K) must be an integer between 1 and 50');
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
        rag_mode: form.rag_mode,
        rag_top_k: ragTopK,
      });
      ingest(updated);
      toast.success('Settings saved');
    } catch (err) {
      toast.error(errorMessage(err));
    } finally {
      setSaving(false);
    }
  };

  const saveLlm = async () => {
    if (!llm) return;
    const temperature = Number(llm.temperature);
    const maxTokens = Number(llm.max_tokens);
    const timeout = Number(llm.timeout_seconds);
    if (Number.isNaN(temperature) || temperature < 0 || temperature > 2) {
      toast.error('Temperature must be between 0 and 2');
      return;
    }
    if (!Number.isInteger(maxTokens) || maxTokens < 64 || maxTokens > 8192) {
      toast.error('Max tokens must be an integer between 64 and 8192');
      return;
    }
    if (Number.isNaN(timeout) || timeout < 1 || timeout > 600) {
      toast.error('Timeout must be between 1 and 600 seconds');
      return;
    }
    if (llm.provider === 'openai' && !llm.base_url.trim()) {
      toast.error('Base URL is required for the OpenAI-compatible provider');
      return;
    }
    if (llm.embed_provider === 'openai' && !llm.embed_base_url.trim() && !llm.base_url.trim()) {
      toast.error('Embedding base URL (or the chat base URL) is required for the OpenAI embedder');
      return;
    }
    if (llm.embed_provider === 'openai' && !llm.embed_model.trim()) {
      toast.error('Embedding model is required for the OpenAI embedder');
      return;
    }
    const usesAzure = llm.provider === 'azure' || llm.embed_provider === 'azure';
    if (usesAzure && !llm.azure_endpoint.trim()) {
      toast.error('Azure OpenAI endpoint is required when a provider is Azure');
      return;
    }
    if (llm.provider === 'azure' && !llm.model.trim()) {
      toast.error('Model (the Azure chat deployment name) is required for Azure chat');
      return;
    }
    if (llm.embed_provider === 'azure' && !llm.embed_model.trim()) {
      toast.error('Embedding model (the Azure embed deployment name) is required for Azure embeddings');
      return;
    }
    const body: LlmConfigInput = {
      llm_provider: llm.provider,
      genai_model: llm.model.trim() || null,
      genai_base_url: llm.base_url.trim() || null,
      genai_temperature: temperature,
      genai_max_tokens: maxTokens,
      genai_timeout_seconds: timeout,
      genai_auth_scheme: llm.auth_scheme,
      genai_api_key_env: llm.api_key_env.trim() || null,
      genai_embed_provider: llm.embed_provider,
      genai_embed_model: llm.embed_model.trim() || null,
      genai_embed_base_url: llm.embed_base_url.trim() || null,
      genai_embed_api_key_env: llm.embed_api_key_env.trim() || null,
      azure_openai_endpoint: llm.azure_endpoint.trim() || null,
      azure_openai_api_version: llm.azure_api_version.trim() || null,
      azure_openai_api_key_env: llm.azure_api_key_env.trim() || null,
      azure_openai_reasoning: llm.azure_reasoning,
    };
    setSavingLlm(true);
    try {
      await api.put('/api/masters/llm-config', body);
      // re-read effective config (the gateway has reloaded)
      const fresh = await api.get<MasterSettings>('/api/masters/settings');
      ingest(fresh);
      toast.success('LLM endpoint saved and applied');
    } catch (err) {
      toast.error(errorMessage(err));
    } finally {
      setSavingLlm(false);
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

        <h3 className="settings-group">Large-document retrieval (RAG)</h3>
        <div className="form-grid-2">
          <div className="field">
            <label>Retrieval mode</label>
            <select
              className="select slim"
              value={form.rag_mode}
              onChange={(e) => set('rag_mode', e.target.value as Editable['rag_mode'])}
            >
              <option value="off">Off — full document text</option>
              <option value="keyword">Keyword — lexical, no embedding model</option>
              <option value="embedding">Embedding — semantic / hybrid</option>
            </select>
          </div>
          <div className="field">
            <label>Passages per document (top-K)</label>
            <input
              className="input slim"
              type="number"
              min={1}
              max={50}
              step={1}
              value={form.rag_top_k}
              onChange={(e) => set('rag_top_k', e.target.value)}
            />
          </div>
        </div>
        <div className="hint">
          For long documents (e.g. 300-page annual reports), each section is grounded on the most
          relevant retrieved passages instead of the first slice of full text.{' '}
          <strong>Keyword</strong> ranks passages lexically (no embedding model needed);{' '}
          <strong>Embedding</strong> uses semantic / hybrid retrieval (needs the embedding endpoint
          configured below). Documents are indexed at upload. Off = full-text grounding.
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
            <span className={`chip ${keyConfigured ? 'chip-green' : 'chip-amber'}`}>
              API key {keyConfigured ? 'configured' : 'not set'}
            </span>
          </div>

          <div className="form-grid-2">
            <div className="field">
              <label>Provider</label>
              <select
                className="select slim"
                value={llm.provider}
                onChange={(e) => setL('provider', e.target.value)}
              >
                <option value="mock">mock (offline, deterministic)</option>
                <option value="anthropic">anthropic (official SDK)</option>
                <option value="openai">openai-compatible endpoint</option>
                <option value="azure">azure openai</option>
              </select>
            </div>
            <div className="field">
              <label>Model {llm.provider === 'azure' ? '(chat deployment name)' : ''}</label>
              <input className="input slim" value={llm.model} onChange={(e) => setL('model', e.target.value)} />
            </div>
          </div>

          <div className="field">
            <label>Base URL {llm.provider === 'openai' ? '(required)' : '(openai only)'}</label>
            <input
              className="input slim"
              placeholder="https://llm.internal/v1"
              value={llm.base_url}
              onChange={(e) => setL('base_url', e.target.value)}
            />
          </div>

          <div className="form-grid-3">
            <div className="field">
              <label>Temperature</label>
              <input className="input slim" type="number" min={0} max={2} step={0.1}
                value={llm.temperature} onChange={(e) => setL('temperature', e.target.value)} />
            </div>
            <div className="field">
              <label>Max tokens</label>
              <input className="input slim" type="number" min={64} max={8192} step={1}
                value={llm.max_tokens} onChange={(e) => setL('max_tokens', e.target.value)} />
            </div>
            <div className="field">
              <label>Timeout (s)</label>
              <input className="input slim" type="number" min={1} max={600} step={1}
                value={llm.timeout_seconds} onChange={(e) => setL('timeout_seconds', e.target.value)} />
            </div>
          </div>

          <div className="form-grid-2">
            <div className="field">
              <label>Auth scheme</label>
              <input className="input slim" placeholder="Bearer"
                value={llm.auth_scheme} onChange={(e) => setL('auth_scheme', e.target.value)} />
            </div>
            <div className="field">
              <label>API key env var</label>
              <input className="input slim mono"
                value={llm.api_key_env} onChange={(e) => setL('api_key_env', e.target.value)} />
            </div>
          </div>

          <h3 className="settings-group">
            Embedding endpoint (for RAG)
            <span className={`chip ${embedKeyConfigured ? 'chip-green' : 'chip-amber'}`}>
              key {embedKeyConfigured ? 'configured' : 'not set'}
            </span>
          </h3>
          <div className="form-grid-2">
            <div className="field">
              <label>Embedding provider</label>
              <select
                className="select slim"
                value={llm.embed_provider}
                onChange={(e) => setL('embed_provider', e.target.value)}
              >
                <option value="mock">mock (offline, deterministic)</option>
                <option value="openai">openai-compatible /embeddings</option>
                <option value="azure">azure openai</option>
              </select>
            </div>
            <div className="field">
              <label>
                Embedding model{' '}
                {llm.embed_provider === 'openai'
                  ? '(required)'
                  : llm.embed_provider === 'azure'
                    ? '(embed deployment name)'
                    : ''}
              </label>
              <input
                className="input slim"
                placeholder="text-embedding-3-small"
                value={llm.embed_model}
                onChange={(e) => setL('embed_model', e.target.value)}
              />
            </div>
          </div>
          <div className="form-grid-2">
            <div className="field">
              <label>Embedding base URL</label>
              <input
                className="input slim"
                placeholder="(defaults to the chat base URL)"
                value={llm.embed_base_url}
                onChange={(e) => setL('embed_base_url', e.target.value)}
              />
            </div>
            <div className="field">
              <label>Embedding API key env var</label>
              <input
                className="input slim mono"
                value={llm.embed_api_key_env}
                onChange={(e) => setL('embed_api_key_env', e.target.value)}
              />
            </div>
          </div>

          {llm.provider === 'azure' || llm.embed_provider === 'azure' ? (
            <>
              <h3 className="settings-group">
                Azure OpenAI
                <span className={`chip ${azureKeyConfigured ? 'chip-green' : 'chip-amber'}`}>
                  key {azureKeyConfigured ? 'configured' : 'not set'}
                </span>
              </h3>
              <div className="form-grid-2">
                <div className="field">
                  <label>Azure OpenAI endpoint</label>
                  <input
                    className="input slim"
                    placeholder="https://my-res.openai.azure.com"
                    value={llm.azure_endpoint}
                    onChange={(e) => setL('azure_endpoint', e.target.value)}
                  />
                </div>
                <div className="field">
                  <label>API version</label>
                  <input
                    className="input slim mono"
                    value={llm.azure_api_version}
                    onChange={(e) => setL('azure_api_version', e.target.value)}
                  />
                </div>
              </div>
              <div className="form-grid-2">
                <div className="field">
                  <label>API key env var</label>
                  <input
                    className="input slim mono"
                    value={llm.azure_api_key_env}
                    onChange={(e) => setL('azure_api_key_env', e.target.value)}
                  />
                </div>
                <div className="field">
                  <label>Reasoning model (o-series)</label>
                  <label className="check-pill" style={{ marginTop: 2 }}>
                    <input
                      type="checkbox"
                      checked={llm.azure_reasoning}
                      onChange={(e) => setL('azure_reasoning', e.target.checked)}
                    />
                    Chat deployment is a reasoning model
                  </label>
                </div>
              </div>
              <div className="hint">
                Chat and embeddings share this endpoint; the model fields above are the Azure{' '}
                <strong>deployment names</strong>. Reasoning (o-series) deployments use{' '}
                <code>max_completion_tokens</code> and omit temperature.
              </div>
            </>
          ) : null}

          <div className="hint">
            The API <strong>key value</strong> is never entered or stored here — set it in the
            environment/vault variable named above (status shown top-right). The embedder can use a
            different backend than chat (e.g. chat on Anthropic, embeddings on an OpenAI-compatible
            or Azure endpoint). Saving applies the config to the gateway immediately, no restart required.
          </div>

          <div className="actions-row">
            <button type="button" className="btn btn-primary" disabled={savingLlm} onClick={saveLlm}>
              {savingLlm ? 'Saving…' : 'Save LLM endpoint'}
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
