type ChipTone = 'green' | 'red' | 'amber' | 'blue' | 'gray' | 'navy' | 'purple';

const TONE_BY_STATUS: Record<string, ChipTone> = {
  // master version lifecycle
  draft: 'amber',
  in_review: 'blue',
  published: 'green',
  retired: 'gray',
  rejected: 'red',
  // run / section states
  queued: 'gray',
  running: 'blue',
  complete: 'green',
  partial: 'amber',
  failed: 'red',
  skipped: 'gray',
  // suggestions
  pending: 'amber',
  accepted: 'green',
  // documents
  quarantined: 'red',
  ready: 'green',
  no_text: 'amber',
  // extraction badges
  ok: 'green',
  empty: 'amber',
  unsupported: 'red',
  // cases / cams
  open: 'blue',
  generating: 'blue',
  finalised: 'green',
  final: 'green',
  // tag / version sources
  auto: 'purple',
  user: 'navy',
  manual: 'navy',
  generated: 'blue',
  chat_suggestion: 'purple',
  regeneration: 'blue',
  // preference scopes / misc
  org_default: 'gray',
  active: 'green',
  inactive: 'gray',
  override: 'amber',
};

interface Props {
  status: string;
  label?: string;
  tone?: ChipTone;
  title?: string;
}

export function StatusChip({ status, label, tone, title }: Props) {
  const resolved = tone ?? TONE_BY_STATUS[status] ?? 'gray';
  return (
    <span className={`chip chip-${resolved}`} title={title}>
      {(label ?? status).replace(/_/g, ' ')}
    </span>
  );
}
