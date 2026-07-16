import type { TemplatePayload, TemplateSectionRow } from '../../../api/types';
import { ChipsInput } from '../../../components/ChipsInput';

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

export function TemplateForm({ value, onChange }: Props) {
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

  return (
    <>
      <div className="form-grid-2">
        <div className="field">
          <label>Name</label>
          <input className="input" value={value.name} onChange={(e) => onChange({ ...value, name: e.target.value })} />
        </div>
        <div className="field">
          <label>Segment</label>
          <select
            className="select"
            value={value.segment}
            onChange={(e) => onChange({ ...value, segment: e.target.value as TemplatePayload['segment'] })}
          >
            <option value="corporate">corporate</option>
            <option value="fi">fi</option>
            <option value="project_finance">project_finance</option>
          </select>
        </div>
      </div>
      <div className="field">
        <label>Relationship</label>
        <select
          className="select"
          value={value.relationship}
          onChange={(e) => onChange({ ...value, relationship: e.target.value as TemplatePayload['relationship'] })}
        >
          <option value="etb">etb</option>
          <option value="ntb">ntb</option>
        </select>
      </div>
      <div className="field">
        <label>Template instructions</label>
        <textarea
          className="textarea mono"
          value={value.template_instructions}
          onChange={(e) => onChange({ ...value, template_instructions: e.target.value })}
        />
      </div>

      <div className="field">
        <label>Sections (ordered)</label>
        <div className="row-editor">
          {value.sections.map((row, idx) => (
            <div key={idx} className="row-editor-row template-section-row">
              <span className="row-order mono">{row.order}</span>
              <input
                className="input mono"
                placeholder="section_code"
                value={row.section_code}
                onChange={(e) => setRow(idx, { section_code: e.target.value })}
              />
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
          ))}
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
        <label>Required doc types</label>
        <ChipsInput
          values={value.required_doc_types}
          onChange={(v) => onChange({ ...value, required_doc_types: v })}
          placeholder="doctype_code…"
        />
      </div>
    </>
  );
}
