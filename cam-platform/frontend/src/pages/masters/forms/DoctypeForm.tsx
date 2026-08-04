import { useState } from 'react';
import { ChevronDown, ChevronRight } from 'lucide-react';
import type { DoctypePayload } from '../../../api/types';
import { ChipsInput } from '../../../components/ChipsInput';
import { InfoTip } from '../../../components/InfoTip';

interface Props {
  value: DoctypePayload;
  onChange: (v: DoctypePayload) => void;
  isNew: boolean;
}

export function DoctypeForm({ value, onChange, isNew }: Props) {
  const [constraintsOpen, setConstraintsOpen] = useState(Boolean(value.file_constraints));
  const fc = value.file_constraints ?? { formats: [], max_mb: 25, max_count: 10 };

  return (
    <>
      <div className="form-grid-2">
        <div className="field">
          <label>Code {isNew ? <span className="hint-inline">(becomes the item key)</span> : null}</label>
          <input className="input mono" value={value.code} disabled={!isNew} onChange={(e) => onChange({ ...value, code: e.target.value })} />
        </div>
        <div className="field">
          <label>Name</label>
          <input className="input" value={value.name} onChange={(e) => onChange({ ...value, name: e.target.value })} />
        </div>
      </div>
      <div className="field">
        <label>Description</label>
        <textarea className="textarea" value={value.description} onChange={(e) => onChange({ ...value, description: e.target.value })} />
      </div>
      <div className="field">
        <label>Synonyms</label>
        <ChipsInput values={value.synonyms} onChange={(v) => onChange({ ...value, synonyms: v })} placeholder="synonym…" />
      </div>
      <div className="field">
        <label>
          Keywords
          <InfoTip
            label="Keywords"
            text="Terms used by the keyword classifier (and as a fallback when AI tagging is off) to auto-detect this document type from a file's text."
          />
        </label>
        <ChipsInput values={value.keywords} onChange={(v) => onChange({ ...value, keywords: v })} placeholder="keyword…" />
      </div>
      <div className="field">
        <label>
          Feeds sections (optional)
          <InfoTip
            label="Feeds sections"
            text="Memo sections this document type typically grounds. Advisory metadata that helps map documents to sections."
          />
        </label>
        <ChipsInput values={value.feeds_sections ?? []} onChange={(v) => onChange({ ...value, feeds_sections: v })} placeholder="section_code…" />
      </div>
      <label className="check-pill standalone">
        <input type="checkbox" checked={value.active} onChange={(e) => onChange({ ...value, active: e.target.checked })} />
        Active
      </label>
      <button type="button" className="collapsible-toggle" onClick={() => setConstraintsOpen(!constraintsOpen)}>
        {constraintsOpen ? <ChevronDown size={13} strokeWidth={2.2} /> : <ChevronRight size={13} strokeWidth={2.2} />} File constraints (optional)
      </button>
      {constraintsOpen ? (
        <div className="collapsible-body">
          <div className="field">
            <label>Formats</label>
            <ChipsInput
              values={fc.formats}
              onChange={(formats) => onChange({ ...value, file_constraints: { ...fc, formats } })}
              placeholder=".pdf…"
            />
          </div>
          <div className="form-grid-2">
            <div className="field">
              <label>Max size (MB)</label>
              <input
                className="input"
                type="number"
                value={fc.max_mb}
                onChange={(e) => onChange({ ...value, file_constraints: { ...fc, max_mb: Number(e.target.value) } })}
              />
            </div>
            <div className="field">
              <label>Max count</label>
              <input
                className="input"
                type="number"
                value={fc.max_count}
                onChange={(e) => onChange({ ...value, file_constraints: { ...fc, max_count: Number(e.target.value) } })}
              />
            </div>
          </div>
          <button type="button" className="btn btn-sm" onClick={() => onChange({ ...value, file_constraints: null })}>
            Clear constraints
          </button>
        </div>
      ) : null}
    </>
  );
}
