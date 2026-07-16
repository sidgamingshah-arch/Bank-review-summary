import { useState } from 'react';

interface Props {
  values: string[];
  onChange: (values: string[]) => void;
  placeholder?: string;
  disabled?: boolean;
}

/** Free-text chips input (Enter or comma adds a token). */
export function ChipsInput({ values, onChange, placeholder, disabled }: Props) {
  const [draft, setDraft] = useState('');

  const commit = () => {
    const token = draft.trim().replace(/,+$/, '');
    if (token && !values.includes(token)) {
      onChange([...values, token]);
    }
    setDraft('');
  };

  return (
    <div className={`chips-input${disabled ? ' chips-disabled' : ''}`}>
      {values.map((v) => (
        <span key={v} className="chip-token">
          {v}
          {!disabled && (
            <button type="button" aria-label={`Remove ${v}`} onClick={() => onChange(values.filter((x) => x !== v))}>
              ✕
            </button>
          )}
        </span>
      ))}
      {!disabled && (
        <input
          value={draft}
          placeholder={placeholder ?? 'Add…'}
          onChange={(e) => {
            if (e.target.value.endsWith(',')) {
              setDraft(e.target.value);
              window.setTimeout(commit, 0);
            } else {
              setDraft(e.target.value);
            }
          }}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault();
              commit();
            } else if (e.key === 'Backspace' && draft === '' && values.length > 0) {
              onChange(values.slice(0, -1));
            }
          }}
          onBlur={commit}
        />
      )}
    </div>
  );
}
