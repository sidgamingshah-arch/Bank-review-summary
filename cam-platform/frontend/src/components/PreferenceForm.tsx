import type { PreferenceProfileInput } from '../api/types';

interface Props {
  value: PreferenceProfileInput;
  onChange: (value: PreferenceProfileInput) => void;
  disabled?: boolean;
  idPrefix?: string;
}

interface GroupSpec {
  field: keyof PreferenceProfileInput;
  label: string;
  options: { value: string; label: string }[];
}

const GROUPS: GroupSpec[] = [
  {
    field: 'tonality',
    label: 'Tonality',
    options: [
      { value: 'crisp', label: 'Crisp' },
      { value: 'narrative', label: 'Narrative' },
    ],
  },
  {
    field: 'structure_bias',
    label: 'Structure bias',
    options: [
      { value: 'bullets', label: 'Bullets' },
      { value: 'paragraphs', label: 'Paragraphs' },
    ],
  },
  {
    field: 'table_usage',
    label: 'Table usage',
    options: [
      { value: 'auto', label: 'Auto' },
      { value: 'prefer', label: 'Prefer tables' },
      { value: 'avoid', label: 'Avoid tables' },
    ],
  },
  {
    field: 'length',
    label: 'Length',
    options: [
      { value: 'concise', label: 'Concise' },
      { value: 'standard', label: 'Standard' },
      { value: 'detailed', label: 'Detailed' },
    ],
  },
];

/** Shared preference selector — used on /preferences, org defaults and run overrides. */
export function PreferenceForm({ value, onChange, disabled, idPrefix = 'pref' }: Props) {
  return (
    <div className="pref-form">
      {GROUPS.map((group) => (
        <div className="field" key={group.field}>
          <label>{group.label}</label>
          <div className="radio-group">
            {group.options.map((opt) => {
              const id = `${idPrefix}-${group.field}-${opt.value}`;
              return (
                <label key={opt.value} htmlFor={id} className={`radio-pill${value[group.field] === opt.value ? ' selected' : ''}`}>
                  <input
                    type="radio"
                    id={id}
                    name={`${idPrefix}-${group.field}`}
                    checked={value[group.field] === opt.value}
                    disabled={disabled}
                    onChange={() => onChange({ ...value, [group.field]: opt.value })}
                  />
                  {opt.label}
                </label>
              );
            })}
          </div>
        </div>
      ))}
    </div>
  );
}
