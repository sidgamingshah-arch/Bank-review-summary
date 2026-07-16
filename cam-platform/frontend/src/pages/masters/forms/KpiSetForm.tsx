import type { KpiRow, KpiSetPayload } from '../../../api/types';
import { ChipsInput } from '../../../components/ChipsInput';

interface Props {
  value: KpiSetPayload;
  onChange: (v: KpiSetPayload) => void;
  isNew: boolean;
}

const EMPTY_KPI: KpiRow = {
  code: '',
  name: '',
  definition: '',
  unit: '',
  polarity: 'higher_better',
  benchmark: null,
  sections: [],
};

export function KpiSetForm({ value, onChange, isNew }: Props) {
  const setRow = (idx: number, patch: Partial<KpiRow>) => {
    onChange({ ...value, kpis: value.kpis.map((k, i) => (i === idx ? { ...k, ...patch } : k)) });
  };

  return (
    <>
      <div className="field">
        <label>Industry code {isNew ? <span className="hint-inline">(becomes the item key)</span> : null}</label>
        <input
          className="input mono"
          value={value.industry_code}
          disabled={!isNew}
          onChange={(e) => onChange({ ...value, industry_code: e.target.value })}
        />
      </div>
      <div className="field">
        <label>KPIs</label>
        <div className="row-editor">
          {value.kpis.map((kpi, idx) => (
            <div key={idx} className="kpi-row-card">
              <div className="form-grid-3">
                <div className="field">
                  <label>Code</label>
                  <input className="input mono" value={kpi.code} onChange={(e) => setRow(idx, { code: e.target.value })} />
                </div>
                <div className="field">
                  <label>Name</label>
                  <input className="input" value={kpi.name} onChange={(e) => setRow(idx, { name: e.target.value })} />
                </div>
                <div className="field">
                  <label>Unit</label>
                  <input className="input" value={kpi.unit} onChange={(e) => setRow(idx, { unit: e.target.value })} />
                </div>
              </div>
              <div className="field">
                <label>Definition</label>
                <input className="input" value={kpi.definition} onChange={(e) => setRow(idx, { definition: e.target.value })} />
              </div>
              <div className="form-grid-2">
                <div className="field">
                  <label>Polarity</label>
                  <select
                    className="select"
                    value={kpi.polarity}
                    onChange={(e) => setRow(idx, { polarity: e.target.value as KpiRow['polarity'] })}
                  >
                    <option value="higher_better">higher_better</option>
                    <option value="lower_better">lower_better</option>
                  </select>
                </div>
                <div className="field">
                  <label>Benchmark (optional)</label>
                  <input
                    className="input"
                    value={kpi.benchmark ?? ''}
                    onChange={(e) => setRow(idx, { benchmark: e.target.value || null })}
                  />
                </div>
              </div>
              <div className="field">
                <label>Sections</label>
                <ChipsInput values={kpi.sections} onChange={(sections) => setRow(idx, { sections })} placeholder="section_code…" />
              </div>
              <button
                type="button"
                className="btn btn-sm btn-danger"
                onClick={() => onChange({ ...value, kpis: value.kpis.filter((_, i) => i !== idx) })}
              >
                Remove KPI
              </button>
            </div>
          ))}
          <button type="button" className="btn btn-sm" onClick={() => onChange({ ...value, kpis: [...value.kpis, { ...EMPTY_KPI }] })}>
            + Add KPI
          </button>
        </div>
      </div>
    </>
  );
}
