import { useEffect, useState } from 'react';
import { api } from '../../../api/client';
import type {
  CodeListOption,
  PublishedCodeList,
  PublishedSection,
  TemplatePayload,
  TemplateSectionRow,
} from '../../../api/types';
import { ChipsInput } from '../../../components/ChipsInput';
import { InfoTip } from '../../../components/InfoTip';

interface Props {
  value: TemplatePayload;
  onChange: (v: TemplatePayload) => void;
}

const EMPTY_ROW = (order: number): TemplateSectionRow => ({
  order,
  section_code: '',
  mandatory: true,
  include_if_doctype: null,
  length_guidance: '',
  fixed_format: false,
});

/** Options for a code-list-backed select, always including the current value so an
 *  existing template keeps its selection even if that code was later deactivated. */
function withCurrent(options: CodeListOption[], current: string): CodeListOption[] {
  if (!current || options.some((o) => o.code === current)) return options;
  return [{ code: current, label: `${current} (not in list)` }, ...options];
}

export function TemplateForm({ value, onChange }: Props) {
  const [segments, setSegments] = useState<CodeListOption[]>([]);
  const [relationships, setRelationships] = useState<CodeListOption[]>([]);
  const [sections, setSections] = useState<PublishedSection[]>([]);

  useEffect(() => {
    api.get<PublishedCodeList>('/api/masters/published/codelist/segment')
      .then((r) => setSegments(r.entries)).catch(() => setSegments([]));
    api.get<PublishedCodeList>('/api/masters/published/codelist/relationship')
      .then((r) => setRelationships(r.entries)).catch(() => setRelationships([]));
    api.get<PublishedSection[]>('/api/masters/published/sections')
      .then(setSections).catch(() => setSections([]));
  }, []);

  const setRow = (idx: number, patch: Partial<TemplateSectionRow>) => {
    onChange({ ...value, sections: value.sections.map((r, i) => (i === idx ? { ...r, ...patch } : r)) });
  };
  const move = (idx: number, dir: -1 | 1) => {
    const target = idx + dir;
    if (target < 0 || target >= value.sections.length) return;
    const rows = [...value.sections];
    [rows[idx], rows[target]] = [rows[target], rows[idx]];
    onChange({ ...value, sections: rows.map((r, i) => ({ ...r, order: i + 1 })) });
  };
  const removeRow = (idx: number) => {
    onChange({
      ...value,
      sections: value.sections.filter((_, i) => i !== idx).map((r, i) => ({ ...r, order: i + 1 })),
    });
  };

  const segOptions = withCurrent(segments, value.segment);
  const relOptions = withCurrent(relationships, value.relationship);

  return (
    <>
      <div className="form-grid-2">
        <div className="field">
          <label>Name</label>
          <input className="input" value={value.name} onChange={(e) => onChange({ ...value, name: e.target.value })} />
        </div>
        <div className="field">
          <label>
            Segment
            <InfoTip
              label="Segment"
              text="The lending segment this template applies to (e.g. Corporate, FI). Managed in the Code lists master under 'segment'."
            />
          </label>
          <select className="select" value={value.segment} onChange={(e) => onChange({ ...value, segment: e.target.value })}>
            {segOptions.length === 0 ? <option value="">(no segment code list published)</option> : null}
            {segOptions.map((o) => (
              <option key={o.code} value={o.code}>{o.label}</option>
            ))}
          </select>
        </div>
      </div>
      <div className="field">
        <label>
          Relationship
          <InfoTip
            label="Relationship"
            text="Borrower relationship with the bank — ETB (Existing to Bank) or NTB (New to Bank). Managed in the Code lists master under 'relationship'."
          />
        </label>
        <select
          className="select"
          value={value.relationship}
          onChange={(e) => onChange({ ...value, relationship: e.target.value })}
        >
          {relOptions.length === 0 ? <option value="">(no relationship code list published)</option> : null}
          {relOptions.map((o) => (
            <option key={o.code} value={o.code}>{o.label}</option>
          ))}
        </select>
      </div>
      <div className="field">
        <label>
          Template instructions
          <InfoTip
            label="Template instructions"
            text="House-style guidance prepended to every section's prompt for this template (tone, units, language). Supports {{placeholders}}."
          />
        </label>
        <textarea
          className="textarea mono"
          value={value.template_instructions}
          onChange={(e) => onChange({ ...value, template_instructions: e.target.value })}
        />
      </div>

      <div className="field">
        <label>
          Sections (ordered)
          <InfoTip
            label="Sections"
            text="The memo's sections, in display order. Each is a published section prompt; pick from the dropdown. 'include if doctype' makes a section conditional; 'fixed' suppresses style preferences."
          />
        </label>
        <div className="row-editor">
          {value.sections.map((row, idx) => {
            const opts = sections.some((s) => s.code === row.section_code) || !row.section_code
              ? sections
              : [{ code: row.section_code, name: `${row.section_code} (unpublished)` }, ...sections];
            return (
              <div key={idx} className="row-editor-row template-section-row">
                <span className="row-order mono">{row.order}</span>
                <select
                  className="select"
                  value={row.section_code}
                  onChange={(e) => setRow(idx, { section_code: e.target.value })}
                >
                  <option value="">— select section —</option>
                  {opts.map((s) => (
                    <option key={s.code} value={s.code}>{`${s.name} (${s.code})`}</option>
                  ))}
                </select>
                <label className="check-pill">
                  <input type="checkbox" checked={row.mandatory} onChange={(e) => setRow(idx, { mandatory: e.target.checked })} />
                  mandatory
                </label>
                <input
                  className="input mono"
                  placeholder="include_if_doctype"
                  title="Only include this section when a document of this type is present"
                  value={row.include_if_doctype ?? ''}
                  onChange={(e) => setRow(idx, { include_if_doctype: e.target.value || null })}
                />
                <input
                  className="input"
                  placeholder="length guidance"
                  value={row.length_guidance ?? ''}
                  onChange={(e) => setRow(idx, { length_guidance: e.target.value })}
                />
                <label className="check-pill">
                  <input type="checkbox" checked={row.fixed_format} onChange={(e) => setRow(idx, { fixed_format: e.target.checked })} />
                  fixed
                </label>
                <span className="btn-row">
                  <button type="button" className="btn btn-sm" onClick={() => move(idx, -1)} disabled={idx === 0} aria-label="Move up">
                    ↑
                  </button>
                  <button
                    type="button"
                    className="btn btn-sm"
                    onClick={() => move(idx, 1)}
                    disabled={idx === value.sections.length - 1}
                    aria-label="Move down"
                  >
                    ↓
                  </button>
                  <button type="button" className="btn btn-sm btn-danger" onClick={() => removeRow(idx)} aria-label="Remove row">
                    ✕
                  </button>
                </span>
              </div>
            );
          })}
          <button
            type="button"
            className="btn btn-sm"
            onClick={() => onChange({ ...value, sections: [...value.sections, EMPTY_ROW(value.sections.length + 1)] })}
          >
            + Add section
          </button>
        </div>
      </div>

      <div className="field">
        <label>
          Required doc types
          <InfoTip
            label="Required doc types"
            text="Document types expected for a complete run; missing ones surface as data gaps in the memo trailer."
          />
        </label>
        <ChipsInput
          values={value.required_doc_types}
          onChange={(v) => onChange({ ...value, required_doc_types: v })}
          placeholder="doctype_code…"
        />
      </div>
    </>
  );
}
