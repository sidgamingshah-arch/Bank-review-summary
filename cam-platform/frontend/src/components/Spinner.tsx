interface Props {
  small?: boolean;
  label?: string;
}

export function Spinner({ small, label }: Props) {
  return (
    <span className={`spinner-wrap${small ? ' spinner-sm' : ''}`}>
      <span className="spinner" aria-hidden="true" />
      {label ? <span className="spinner-label">{label}</span> : null}
    </span>
  );
}

export function PageLoading({ label = 'Loading…' }: { label?: string }) {
  return (
    <div className="page-loading">
      <Spinner label={label} />
    </div>
  );
}
