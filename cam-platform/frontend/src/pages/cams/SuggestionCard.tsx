import type { Suggestion } from '../../api/types';
import { DiffView } from '../../components/DiffView';
import { StatusChip } from '../../components/StatusChip';

interface Props {
  suggestion: Suggestion;
  sectionName?: string;
  busy: boolean;
  enabled: boolean;
  onAccept: (s: Suggestion) => void;
  onReject: (s: Suggestion) => void;
}

export function SuggestionCard({ suggestion, sectionName, busy, enabled, onAccept, onReject }: Props) {
  return (
    <div className="suggestion-card">
      <div className="suggestion-head">
        <StatusChip status={suggestion.status} label={`suggestion: ${suggestion.status}`} />
        {sectionName ? <span className="muted">for {sectionName}</span> : null}
      </div>
      <div className="suggestion-instruction">{suggestion.instruction}</div>
      <DiffView diff={suggestion.diff} />
      {suggestion.status === 'pending' && enabled ? (
        <div className="btn-row suggestion-actions">
          <button type="button" className="btn btn-sm btn-primary" disabled={busy} onClick={() => onAccept(suggestion)}>
            Accept
          </button>
          <button type="button" className="btn btn-sm btn-danger" disabled={busy} onClick={() => onReject(suggestion)}>
            Reject
          </button>
        </div>
      ) : null}
    </div>
  );
}
