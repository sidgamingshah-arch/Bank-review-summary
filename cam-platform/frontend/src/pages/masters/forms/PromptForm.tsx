import { useState } from 'react';
import type { PromptPayload } from '../../../api/types';
import { ChipsInput } from '../../../components/ChipsInput';

interface Props {
  value: PromptPayload;
  onChange: (v: PromptPayload) => void;
  isNew: boolean;
}

export function PromptForm({ value, onChange, isNew }: Props) {
  const [overridesOpen, setOverridesOpen] = useState(Boolean(value.model_overrides));
  const overrides = value.model_overrides ?? {};

  const setOverride = (field: 'model' | 'temperature' | 'max_tokens', raw: string) => {
    const next = { ...overrides } as Record<string, unknown>;
    if (raw === '') {
      delete next[field];
    } else {
      next[field] = field === 'model' ? raw : Number(raw);
    }
    onChange({ ...value, model_overrides: Object.keys(next).length > 0 ? next : undefined });
  };

  return (
    <>
      <div className="form-grid-2">
        <div className="field">
          <label>Section code {isNew ? <span className="hint-inline">(becomes the item key)</span> : null}</label>
          <input
            className="input mono"
            value={value.section_code}
            disabled={!isNew}
            onChange={(e) => onChange({ ...value, section_code: e.target.value })}
          />
        </div>
        <div className="field">
          <label>Section name</label>
          <input className="input" value={value.section_name} onChange={(e) => onChange({ ...value, section_name: e.target.value })} />
        </div>
      </div>
      <div className="field">
        <label>Scope</label>
        <select className="select" value={value.scope} onChange={(e) => onChange({ ...value, scope: e.target.value as 'section' | 'global' })}>
          <option value="section">section</option>
          <option value="global">global</option>
        </select>
      </div>
      <div className="field">
        <label>Prompt text</label>
        <textarea
          className="textarea tall mono"
          value={value.prompt_text}
          onChange={(e) => onChange({ ...value, prompt_text: e.target.value })}
        />
        <div className="hint">
          Allowed placeholders: {'{{borrower_name}} {{case_type}} {{relationship}} {{industry_name}} {{industry_kpis}} {{doc:<doctype_code>}} {{today}}'}
        </div>
      </div>
      <div className="field">
        <label>Source doc types</label>
        <ChipsInput
          values={value.source_doc_types}
          onChange={(v) => onChange({ ...value, source_doc_types: v })}
          placeholder="doctype_code…"
        />
      </div>
      <label className="check-pill standalone">
        <input
          type="checkbox"
          checked={value.uses_industry_kpis}
          onChange={(e) => onChange({ ...value, uses_industry_kpis: e.target.checked })}
        />
        Uses industry KPIs
      </label>
      <div className="field">
        <label>Rendering hints (optional)</label>
        <input
          className="input"
          value={value.rendering_hints ?? ''}
          onChange={(e) => onChange({ ...value, rendering_hints: e.target.value || undefined })}
        />
      </div>
      <button type="button" className="collapsible-toggle" onClick={() => setOverridesOpen(!overridesOpen)}>
        {overridesOpen ? '▾' : '▸'} Model overrides (optional)
      </button>
      {overridesOpen ? (
        <div className="collapsible-body form-grid-3">
          <div className="field">
            <label>Model</label>
            <input className="input mono" value={overrides.model ?? ''} onChange={(e) => setOverride('model', e.target.value)} />
          </div>
          <div className="field">
            <label>Temperature</label>
            <input
              className="input"
              type="number"
              step="0.1"
              value={overrides.temperature ?? ''}
              onChange={(e) => setOverride('temperature', e.target.value)}
            />
          </div>
          <div className="field">
            <label>Max tokens</label>
            <input
              className="input"
              type="number"
              value={overrides.max_tokens ?? ''}
              onChange={(e) => setOverride('max_tokens', e.target.value)}
            />
          </div>
        </div>
      ) : null}
    </>
  );
}
