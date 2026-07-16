import { useEffect, useState } from 'react';
import { api, errorMessage } from '../../api/client';
import type { CamSection, DiffResult, SectionVersion } from '../../api/types';
import { DiffView } from '../../components/DiffView';
import { Spinner } from '../../components/Spinner';
import { StatusChip } from '../../components/StatusChip';
import { useToast } from '../../components/Toast';

interface Props {
  camId: string;
  section: CamSection;
  onClose: () => void;
}

export function HistoryDrawer({ camId, section, onClose }: Props) {
  const toast = useToast();
  const [versions, setVersions] = useState<SectionVersion[] | null>(null);
  const [picked, setPicked] = useState<number[]>([]);
  const [diff, setDiff] = useState<string | null>(null);
  const [diffLabel, setDiffLabel] = useState('');
  const [loadingDiff, setLoadingDiff] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api
      .get<SectionVersion[]>(`/api/cams/${camId}/sections/${section.id}/versions`)
      .then((v) => {
        if (!cancelled) setVersions(v);
      })
      .catch((err) => toast.error(errorMessage(err)));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [camId, section.id]);

  const togglePick = (no: number) => {
    setDiff(null);
    setPicked((cur) => {
      if (cur.includes(no)) return cur.filter((n) => n !== no);
      if (cur.length >= 2) return [cur[1], no];
      return [...cur, no];
    });
  };

  const compare = async () => {
    const [a, b] = [...picked].sort((x, y) => x - y);
    setLoadingDiff(true);
    try {
      const res = await api.get<DiffResult>(`/api/cams/${camId}/sections/${section.id}/diff?from=${a}&to=${b}`);
      setDiff(res.diff);
      setDiffLabel(`v${a} → v${b}`);
    } catch (err) {
      toast.error(errorMessage(err));
    } finally {
      setLoadingDiff(false);
    }
  };

  const sorted = versions ? [...versions].sort((a, b) => b.version_no - a.version_no) : null;

  return (
    <div className="drawer-overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="drawer" role="dialog" aria-label={`Version history — ${section.name}`}>
        <div className="drawer-head">
          <h3>History — {section.name}</h3>
          <button type="button" className="btn btn-ghost btn-sm" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </div>
        <div className="drawer-body">
          {!sorted ? (
            <Spinner label="Loading versions…" />
          ) : (
            <>
              <p className="hint">Pick two versions to compare.</p>
              <div className="table-wrap">
                <table className="table">
                  <thead>
                    <tr>
                      <th></th>
                      <th>No</th>
                      <th>Name</th>
                      <th>Source</th>
                      <th>By</th>
                      <th>At</th>
                    </tr>
                  </thead>
                  <tbody>
                    {sorted.map((v) => (
                      <tr key={v.version_no}>
                        <td>
                          <input
                            type="checkbox"
                            checked={picked.includes(v.version_no)}
                            onChange={() => togglePick(v.version_no)}
                            aria-label={`Select version ${v.version_no}`}
                          />
                        </td>
                        <td className="mono">
                          v{v.version_no}
                          {v.version_no === section.current_version_no ? <span className="chip chip-green"> current</span> : null}
                        </td>
                        <td>{v.name ?? <span className="muted">autosave</span>}</td>
                        <td>
                          <StatusChip status={v.source} />
                        </td>
                        <td>{v.created_by}</td>
                        <td className="muted">{new Date(v.created_at).toLocaleString()}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="actions-row">
                <button type="button" className="btn btn-primary btn-sm" disabled={picked.length !== 2 || loadingDiff} onClick={compare}>
                  {loadingDiff ? 'Comparing…' : 'Compare selected'}
                </button>
              </div>
              {diff !== null ? (
                <>
                  <h4 className="diff-title">Diff {diffLabel}</h4>
                  <DiffView diff={diff} />
                </>
              ) : null}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
