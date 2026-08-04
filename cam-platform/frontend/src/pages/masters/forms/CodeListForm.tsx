import type { CodeListEntryRow, CodeListPayload } from '../../../api/types';
import { InfoTip } from '../../../components/InfoTip';

interface Props {
  value: CodeListPayload;
  onChange: (v: CodeListPayload) => void;
  isNew?: boolean;
}

const EMPTY = (order: number): CodeListEntryRow => ({ code: '', label: '', active: true, order });

export function CodeListForm({ value, onChange, isNew }: Props) {
  const setEntry = (i: number, patch: Partial<CodeListEntryRow>) =>
    onChange({ ...value, entries: value.entries.map((e, idx) => (idx === i ? { ...e, ...patch } : e)) });
  const remove = (i: number) =>
    onChange({ ...value, entries: value.entries.filter((_, idx) => idx !== i) });

  return (
    <>
      <div className="field">
        <label>
          List key
          <InfoTip
            label="List key"
            text="The list's identifier. 'segment' and 'relationship' drive the template dropdowns; other lists are reference data. Cannot be changed after creation."
          />
        </label>
        <input
          className="input mono"
          value={value.name}
          disabled={!isNew}
          placeholder="e.g. segment"
          onChange={(e) => onChange({ ...value, name: e.target.value })}
        />
      </div>
      <div className="field">
        <label>Description</label>
        <input
          className="input"
          value={value.description}
          placeholder="What this list is for"
          onChange={(e) => onChange({ ...value, description: e.target.value })}
        />
      </div>
      <div className="field">
        <label>
          Entries
          <InfoTip
            label="Entries"
            text="Each entry has a code (the stored value) and a label (shown to users). Inactive entries are hidden from dropdowns but kept for history. Order sets the dropdown order."
          />
        </label>
        <div className="row-editor">
          {value.entries.map((row, i) => (
            <div key={i} className="row-editor-row">
              <input
                className="input mono"
                placeholder="code"
                value={row.code}
                onChange={(e) => setEntry(i, { code: e.target.value })}
              />
              <input
                className="input"
                placeholder="label"
                value={row.label}
                onChange={(e) => setEntry(i, { label: e.target.value })}
              />
              <input
                className="input slim"
                type="number"
                min={0}
                title="Sort order"
                value={row.order}
                onChange={(e) => setEntry(i, { order: Number(e.target.value) })}
              />
              <label className="check-pill">
                <input type="checkbox" checked={row.active} onChange={(e) => setEntry(i, { active: e.target.checked })} />
                active
              </label>
              <button type="button" className="btn btn-sm btn-danger" onClick={() => remove(i)} aria-label="Remove entry">
                ✕
              </button>
            </div>
          ))}
          <button
            type="button"
            className="btn btn-sm"
            onClick={() => onChange({ ...value, entries: [...value.entries, EMPTY(value.entries.length + 1)] })}
          >
            + Add entry
          </button>
        </div>
      </div>
    </>
  );
}
