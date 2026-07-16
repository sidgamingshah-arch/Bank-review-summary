import { useCallback, useEffect, useState } from 'react';
import { api, errorMessage } from '../../api/client';
import type { DiffResult, ItemDetail, MasterType, Version, VersionMeta } from '../../api/types';
import { DiffView } from '../../components/DiffView';
import { Modal } from '../../components/Modal';
import { Spinner } from '../../components/Spinner';
import { StatusChip } from '../../components/StatusChip';
import { useToast } from '../../components/Toast';
import { VersionEditorModal } from './VersionEditorModal';
import { SandboxModal } from './SandboxModal';

interface Props {
  mtype: MasterType;
  itemKey: string;
  onChanged: () => void;
}

export function MasterDetail({ mtype, itemKey, onChanged }: Props) {
  const toast = useToast();
  const [detail, setDetail] = useState<ItemDetail | null>(null);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [viewVersion, setViewVersion] = useState<Version | null>(null);
  const [rejecting, setRejecting] = useState<VersionMeta | null>(null);
  const [rejectReason, setRejectReason] = useState('');
  const [newVersionBase, setNewVersionBase] = useState<Version | null>(null);
  const [diffFrom, setDiffFrom] = useState<number | ''>('');
  const [diffTo, setDiffTo] = useState<number | ''>('');
  const [diff, setDiff] = useState<string | null>(null);
  const [diffBusy, setDiffBusy] = useState(false);
  const [sandboxOpen, setSandboxOpen] = useState(false);

  const load = useCallback(async () => {
    try {
      const d = await api.get<ItemDetail>(`/api/masters/${mtype}/${encodeURIComponent(itemKey)}`);
      setDetail(d);
      return d;
    } catch (err) {
      toast.error(errorMessage(err));
      return null;
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mtype, itemKey]);

  useEffect(() => {
    setDetail(null);
    setDiff(null);
    setDiffFrom('');
    setDiffTo('');
    load();
  }, [load]);

  const refresh = async () => {
    await load();
    onChanged();
  };

  const lifecycle = async (versionNo: number, action: 'submit' | 'approve' | 'rollback') => {
    setBusyAction(`${action}-${versionNo}`);
    try {
      await api.post(`/api/masters/${mtype}/${encodeURIComponent(itemKey)}/versions/${versionNo}/${action}`);
      const labels = { submit: 'submitted for review', approve: 'approved & published', rollback: 'cloned into a new draft' };
      toast.success(`Version ${versionNo} ${labels[action]}`);
      await refresh();
    } catch (err) {
      toast.error(errorMessage(err));
    } finally {
      setBusyAction(null);
    }
  };

  const reject = async () => {
    if (!rejecting) return;
    setBusyAction(`reject-${rejecting.version_no}`);
    try {
      await api.post(`/api/masters/${mtype}/${encodeURIComponent(itemKey)}/versions/${rejecting.version_no}/reject`, {
        reason: rejectReason.trim(),
      });
      toast.success(`Version ${rejecting.version_no} rejected`);
      setRejecting(null);
      setRejectReason('');
      await refresh();
    } catch (err) {
      toast.error(errorMessage(err));
    } finally {
      setBusyAction(null);
    }
  };

  const openView = async (versionNo: number) => {
    try {
      setViewVersion(await api.get<Version>(`/api/masters/${mtype}/${encodeURIComponent(itemKey)}/versions/${versionNo}`));
    } catch (err) {
      toast.error(errorMessage(err));
    }
  };

  const openNewVersion = async () => {
    if (!detail || detail.versions.length === 0) return;
    const latest = Math.max(...detail.versions.map((v) => v.version_no));
    try {
      setNewVersionBase(await api.get<Version>(`/api/masters/${mtype}/${encodeURIComponent(itemKey)}/versions/${latest}`));
    } catch (err) {
      toast.error(errorMessage(err));
    }
  };

  const compare = async () => {
    if (diffFrom === '' || diffTo === '') return;
    setDiffBusy(true);
    try {
      const res = await api.get<DiffResult>(
        `/api/masters/${mtype}/${encodeURIComponent(itemKey)}/diff?from=${diffFrom}&to=${diffTo}`,
      );
      setDiff(res.diff);
    } catch (err) {
      toast.error(errorMessage(err));
    } finally {
      setDiffBusy(false);
    }
  };

  if (!detail) return <Spinner label="Loading versions…" />;

  const versions = [...detail.versions].sort((a, b) => b.version_no - a.version_no);

  return (
    <div className="master-detail">
      <div className="card-head">
        <h2 className="mono">{itemKey}</h2>
        <div className="btn-row">
          {mtype === 'prompts' ? (
            <button type="button" className="btn btn-sm" onClick={() => setSandboxOpen(true)}>
              Sandbox test
            </button>
          ) : null}
          <button type="button" className="btn btn-sm btn-primary" onClick={openNewVersion}>
            New version
          </button>
        </div>
      </div>
      <p className="hint">
        Maker-checker: the approver must be a different business admin than the maker. Approving publishes the version and retires the
        previously published one.
      </p>

      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr>
              <th>No</th>
              <th>Status</th>
              <th>Maker</th>
              <th>Approver</th>
              <th>Change note</th>
              <th>Dates</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {versions.map((v) => (
              <tr key={v.version_no}>
                <td className="mono">v{v.version_no}</td>
                <td>
                  <StatusChip status={v.status} />
                </td>
                <td>{v.created_by}</td>
                <td>{v.approved_by ?? '—'}</td>
                <td className="change-note-cell">{v.change_note}</td>
                <td className="muted">
                  {new Date(v.created_at).toLocaleDateString()}
                  {v.approved_at ? ` → ${new Date(v.approved_at).toLocaleDateString()}` : ''}
                </td>
                <td>
                  <span className="btn-row">
                    <button type="button" className="btn btn-sm" onClick={() => openView(v.version_no)}>
                      View
                    </button>
                    {v.status === 'draft' ? (
                      <button
                        type="button"
                        className="btn btn-sm"
                        disabled={busyAction !== null}
                        onClick={() => lifecycle(v.version_no, 'submit')}
                      >
                        Submit
                      </button>
                    ) : null}
                    {v.status === 'in_review' ? (
                      <>
                        <button
                          type="button"
                          className="btn btn-sm btn-primary"
                          disabled={busyAction !== null}
                          onClick={() => lifecycle(v.version_no, 'approve')}
                          title="Approver must differ from maker (maker-checker)"
                        >
                          Approve
                        </button>
                        <button
                          type="button"
                          className="btn btn-sm btn-danger"
                          disabled={busyAction !== null}
                          onClick={() => setRejecting(v)}
                        >
                          Reject
                        </button>
                      </>
                    ) : null}
                    <button
                      type="button"
                      className="btn btn-sm"
                      disabled={busyAction !== null}
                      onClick={() => lifecycle(v.version_no, 'rollback')}
                      title="Clone this version into a new draft"
                    >
                      Rollback
                    </button>
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="diff-picker">
        <span className="muted">Compare versions:</span>
        <select className="select slim" value={diffFrom} onChange={(e) => setDiffFrom(e.target.value === '' ? '' : Number(e.target.value))}>
          <option value="">from…</option>
          {versions.map((v) => (
            <option key={v.version_no} value={v.version_no}>
              v{v.version_no}
            </option>
          ))}
        </select>
        <select className="select slim" value={diffTo} onChange={(e) => setDiffTo(e.target.value === '' ? '' : Number(e.target.value))}>
          <option value="">to…</option>
          {versions.map((v) => (
            <option key={v.version_no} value={v.version_no}>
              v{v.version_no}
            </option>
          ))}
        </select>
        <button type="button" className="btn btn-sm" disabled={diffFrom === '' || diffTo === '' || diffBusy} onClick={compare}>
          {diffBusy ? 'Comparing…' : 'Diff'}
        </button>
      </div>
      {diff !== null ? <DiffView diff={diff} /> : null}

      {viewVersion ? (
        <Modal title={`${itemKey} — v${viewVersion.version_no}`} onClose={() => setViewVersion(null)} wide>
          <div className="chip-row head-chips">
            <StatusChip status={viewVersion.status} />
            <span className="muted">
              maker {viewVersion.created_by} · {new Date(viewVersion.created_at).toLocaleString()}
            </span>
            {viewVersion.approved_by ? <span className="muted">approver {viewVersion.approved_by}</span> : null}
          </div>
          <p className="change-note-view">“{viewVersion.change_note}”</p>
          <pre className="detail-json">{JSON.stringify(viewVersion.payload, null, 2)}</pre>
        </Modal>
      ) : null}

      {rejecting ? (
        <Modal
          title={`Reject v${rejecting.version_no} — ${itemKey}`}
          onClose={() => setRejecting(null)}
          footer={
            <>
              <button type="button" className="btn" onClick={() => setRejecting(null)}>
                Cancel
              </button>
              <button type="button" className="btn btn-danger" disabled={!rejectReason.trim() || busyAction !== null} onClick={reject}>
                Reject version
              </button>
            </>
          }
        >
          <div className="field">
            <label>Reason</label>
            <textarea className="textarea" value={rejectReason} onChange={(e) => setRejectReason(e.target.value)} autoFocus />
          </div>
        </Modal>
      ) : null}

      {newVersionBase ? (
        <VersionEditorModal
          mtype={mtype}
          mode="newVersion"
          itemKey={itemKey}
          initialPayload={newVersionBase.payload}
          onClose={() => setNewVersionBase(null)}
          onSaved={() => refresh()}
        />
      ) : null}

      {sandboxOpen ? <SandboxModal promptKey={itemKey} onClose={() => setSandboxOpen(false)} /> : null}
    </div>
  );
}
