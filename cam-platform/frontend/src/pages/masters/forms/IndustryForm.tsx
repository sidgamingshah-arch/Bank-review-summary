import type { IndustryPayload } from '../../../api/types';

interface Props {
  value: IndustryPayload;
  onChange: (v: IndustryPayload) => void;
  isNew: boolean;
}

export function IndustryForm({ value, onChange, isNew }: Props) {
  return (
    <>
      <div className="form-grid-2">
        <div className="field">
          <label>Sector code</label>
          <input className="input mono" value={value.sector_code} onChange={(e) => onChange({ ...value, sector_code: e.target.value })} />
        </div>
        <div className="field">
          <label>Sector name</label>
          <input className="input" value={value.sector_name} onChange={(e) => onChange({ ...value, sector_name: e.target.value })} />
        </div>
      </div>
      <div className="form-grid-2">
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
          <label>Industry name</label>
          <input className="input" value={value.industry_name} onChange={(e) => onChange({ ...value, industry_name: e.target.value })} />
        </div>
      </div>
    </>
  );
}
