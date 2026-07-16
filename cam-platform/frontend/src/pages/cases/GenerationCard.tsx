import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api, ApiError, errorMessage } from '../../api/client';
import { DEFAULT_PREFERENCES } from '../../api/types';
import type { Completeness, ItemSummary, PreferenceProfile, PreferenceProfileInput, Run } from '../../api/types';
import { PreferenceForm } from '../../components/PreferenceForm';
import { Spinner } from '../../components/Spinner';
import { useToast } from '../../components/Toast';

interface Props {
  caseId: string;
  onRunStarted: () => void;
}

export function GenerationCard({ caseId, onRunStarted }: Props) {
  const toast = useToast();
  const navigate = useNavigate();
  const [templates, setTemplates] = useState<ItemSummary[] | null>(null);
  const [templateKey, setTemplateKey] = useState('');
  const [completeness, setCompleteness] = useState<Completeness | null>(null);
  const [checking, setChecking] = useState(false);
  const [proceedWithGaps, setProceedWithGaps] = useState(false);
  const [overrideOpen, setOverrideOpen] = useState(false);
  const [overridePrefs, setOverridePrefs] = useState<PreferenceProfileInput>(DEFAULT_PREFERENCES);
  const [starting, setStarting] = useState(false);
  const [blockNote, setBlockNote] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .get<ItemSummary[]>('/api/masters/templates')
      .then((items) => {
        if (!cancelled) setTemplates(items.filter((t) => t.published_version !== null));
      })
      .catch((err) => {
        if (!cancelled) {
          setTemplates([]);
          toast.error(errorMessage(err));
        }
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    setCompleteness(null);
    setProceedWithGaps(false);
    setBlockNote(null);
    if (!templateKey) return;
    let cancelled = false;
    setChecking(true);
    api
      .get<Completeness>(`/api/cases/${caseId}/completeness?template_key=${encodeURIComponent(templateKey)}`)
      .then((c) => {
        if (!cancelled) setCompleteness(c);
      })
      .catch((err) => {
        if (!cancelled) toast.error(errorMessage(err));
      })
      .finally(() => {
        if (!cancelled) setChecking(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [templateKey, caseId]);

  const openOverride = async () => {
    if (!overrideOpen) {
      try {
        const prefs = await api.get<PreferenceProfile>('/api/auth/preferences');
        setOverridePrefs({
          tonality: prefs.tonality,
          structure_bias: prefs.structure_bias,
          table_usage: prefs.table_usage,
          length: prefs.length,
        });
      } catch {
        setOverridePrefs(DEFAULT_PREFERENCES);
      }
    }
    setOverrideOpen(!overrideOpen);
  };

  const generate = async () => {
    setStarting(true);
    setBlockNote(null);
    try {
      const body: Record<string, unknown> = {
        case_id: caseId,
        template_key: templateKey,
        proceed_with_gaps: proceedWithGaps,
      };
      if (overrideOpen) body.preference_override = overridePrefs;
      const run = await api.post<Run>('/api/runs', body);
      toast.success('Generation started');
      onRunStarted();
      navigate(`/runs/${run.id}`);
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setBlockNote('Required documents are missing. Tick "Proceed with gaps" to generate anyway — gaps will be disclosed in the CAM.');
        toast.error('Generation blocked: completeness gaps (conflict)');
      } else if (err instanceof ApiError && err.status === 429) {
        setBlockNote('You already have the maximum number of active runs. Wait for one to finish, then try again.');
        toast.error('Rate limited: too many active runs (rate_limited)');
      } else {
        toast.error(errorMessage(err));
      }
    } finally {
      setStarting(false);
    }
  };

  const missing = completeness?.missing ?? [];

  return (
    <div className="card">
      <div className="card-head">
        <h2>Generate CAM</h2>
      </div>

      <div className="field">
        <label>Template</label>
        {templates === null ? (
          <div className="muted">Loading templates…</div>
        ) : templates.length === 0 ? (
          <div className="banner banner-warn slim">No published templates. Ask a business admin to publish one.</div>
        ) : (
          <select className="select" value={templateKey} onChange={(e) => setTemplateKey(e.target.value)}>
            <option value="">Select template…</option>
            {templates.map((t) => (
              <option key={t.key} value={t.key}>
                {t.key} (v{t.published_version})
              </option>
            ))}
          </select>
        )}
      </div>

      {checking ? <Spinner small label="Checking completeness…" /> : null}

      {completeness ? (
        <div className="completeness">
          <div className="completeness-col">
            <div className="completeness-title">Required</div>
            {completeness.required.length === 0 ? <span className="muted">none</span> : null}
            {completeness.required.map((d) => (
              <span key={d} className="chip chip-gray mono">
                {d}
              </span>
            ))}
          </div>
          <div className="completeness-col">
            <div className="completeness-title">Present</div>
            {completeness.present.length === 0 ? <span className="muted">none</span> : null}
            {completeness.present.map((d) => (
              <span key={d} className="chip chip-green mono">
                {d}
              </span>
            ))}
          </div>
          <div className="completeness-col">
            <div className="completeness-title">Missing</div>
            {missing.length === 0 ? <span className="chip chip-green">all present</span> : null}
            {missing.map((d) => (
              <span key={d} className="chip chip-amber mono">
                {d}
              </span>
            ))}
          </div>
        </div>
      ) : null}

      {missing.length > 0 ? (
        <label className="check-pill standalone">
          <input type="checkbox" checked={proceedWithGaps} onChange={(e) => setProceedWithGaps(e.target.checked)} />
          Proceed with gaps (missing inputs will be disclosed in the CAM)
        </label>
      ) : null}

      <button type="button" className="collapsible-toggle" onClick={openOverride}>
        {overrideOpen ? '▾' : '▸'} Override output preferences for this run
      </button>
      {overrideOpen ? (
        <div className="collapsible-body">
          <PreferenceForm value={overridePrefs} onChange={setOverridePrefs} idPrefix="run-override" />
        </div>
      ) : null}

      {blockNote ? <div className="banner banner-warn slim">{blockNote}</div> : null}

      <div className="actions-row">
        <button
          type="button"
          className="btn btn-primary"
          disabled={!templateKey || starting || checking || (missing.length > 0 && !proceedWithGaps)}
          onClick={generate}
        >
          {starting ? 'Starting…' : 'Generate'}
        </button>
        {missing.length > 0 && !proceedWithGaps ? <span className="hint">Blocked by missing documents.</span> : null}
      </div>
    </div>
  );
}
