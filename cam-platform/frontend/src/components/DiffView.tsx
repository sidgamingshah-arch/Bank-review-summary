interface Props {
  diff: string;
}

function lineClass(line: string): string {
  if (line.startsWith('+++') || line.startsWith('---')) return 'diff-line diff-meta';
  if (line.startsWith('@@')) return 'diff-line diff-hunk';
  if (line.startsWith('+')) return 'diff-line diff-add';
  if (line.startsWith('-')) return 'diff-line diff-del';
  return 'diff-line';
}

/** Renders unified-diff text with +/- line colouring. */
export function DiffView({ diff }: Props) {
  if (!diff || diff.trim() === '') {
    return <div className="diff-empty">No differences.</div>;
  }
  const lines = diff.replace(/\n$/, '').split('\n');
  return (
    <pre className="diff-view">
      {lines.map((line, i) => (
        <div key={i} className={lineClass(line)}>
          {line || ' '}
        </div>
      ))}
    </pre>
  );
}
