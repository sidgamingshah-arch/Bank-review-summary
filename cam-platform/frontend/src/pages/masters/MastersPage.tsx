import { useCallback, useEffect, useRef, useState } from 'react';
import { Navigate, useNavigate, useParams } from 'react-router-dom';
import { api, errorMessage } from '../../api/client';
import type { ItemSummary, KpiBulkReport, MasterType } from '../../api/types';
import { EmptyState } from '../../components/EmptyState';
import { Modal } from '../../components/Modal';
import { Spinner } from '../../components/Spinner';
import { StatusChip } from '../../components/StatusChip';
import { useToast } from '../../components/Toast';
import { MasterDetail } from './MasterDetail';
import { VersionEditorModal } from './VersionEditorModal';
import { SettingsTab } from './SettingsTab';

const TABS: { id: string; label: string }[] = [
  { id: 'prompts', label: 'Prompts' },
  { id: 'templates', label: 'Templates' },
  { id: 'doctypes', label: 'Doc Types' },
  { id: 'industries', label: 'Industries' },
  { id: 'kpi-sets', label: 'KPI Sets' },
  { id: 'settings', label: 'Settings' },
];

function MasterWorkbench({ mtype }: { mtype: MasterType }) {
  const toast = useToast();
  const fileInput = useRef<HTMLInputElement>(null);
  const [items, setItems] = useState<ItemSummary[] | null>(null);
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [bulkReport, setBulkReport] = useState<KpiBulkReport | null>(null);
  const [bulkBusy, setBulkBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setItems(await api.get<ItemSummary[]>(`/api/masters/${mtype}`));
    } catch (err) {
      setItems([]);
      toast.error(errorMessage(err));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mtype]);

  useEffect(() => {
    setItems(null);
    setSelectedKey(null);
    load();
  }, [load]);

  const bulkUpload = async (file: File) => {
    setBulkBusy(true);
    try {
      const form = new FormData();
      form.append('file', file);
      const report = await api.postForm<KpiBulkReport>('/api/masters/kpi-sets/bulk', form);
      setBulkReport(report);
      load();
    } catch (err) {
      toast.error(errorMessage(err));
    } finally {
      setBulkBusy(false);
    }
  };

  const exportCsv = async () => {
    try {
      await api.download('/api/masters/kpi-sets/export.csv', 'kpi-sets.csv');
      toast.success('KPI sets exported');
    } catch (err) {
      toast.error(errorMessage(err));
    }
  };

  return (
    <div className="masters-split">
      <div className="masters-list card">
        <div className="card-head">
          <h2>{TABS.find((t) => t.id === mtype)?.label}</h2>
          <div className="btn-row">
            {mtype === 'kpi-sets' ? (
              <>
                <button type="button" className="btn btn-sm" disabled={bulkBusy} onClick={() => fileInput.current?.click()}>
                  {bulkBusy ? 'Uploading…' : 'Bulk upload CSV'}
                </button>
                <input
                  ref={fileInput}
                  type="file"
                  accept=".csv"
                  hidden
                  onChange={(e) => {
                    const f = e.target.files?.[0];
                    if (f) bulkUpload(f);
                    e.target.value = '';
                  }}
                />
                <button type="button" className="btn btn-sm" onClick={exportCsv}>
                  Export CSV
                </button>
              </>
            ) : null}
            <button type="button" className="btn btn-sm btn-primary" onClick={() => setCreating(true)}>
              New
            </button>
          </div>
        </div>
        {items === null ? (
          <Spinner label="Loading…" />
        ) : items.length === 0 ? (
          <EmptyState title="No entries" hint={`Create the first ${mtype.replace('-', ' ')} entry.`} />
        ) : (
          <div className="list-pane">
            {items.map((item) => (
              <button
                key={item.key}
                type="button"
                className={`list-item${selectedKey === item.key ? ' active' : ''}`}
                onClick={() => setSelectedKey(item.key)}
              >
                <span className="list-item-key mono">{item.key}</span>
                <span className="list-item-meta">
                  <span className="muted">
                    v{item.latest_version}
                    {item.published_version !== null ? ` · pub v${item.published_version}` : ''}
                  </span>
                  <StatusChip
                    status={item.published_version !== null ? 'published' : 'draft'}
                    label={item.published_version !== null ? 'published' : 'unpublished'}
                  />
                </span>
              </button>
            ))}
          </div>
        )}
      </div>

      <div className="masters-detail card">
        {selectedKey ? (
          <MasterDetail mtype={mtype} itemKey={selectedKey} onChanged={load} />
        ) : (
          <EmptyState title="Select an entry" hint="Pick an item on the left to manage its versions, or create a new one." />
        )}
      </div>

      {creating ? (
        <VersionEditorModal
          mtype={mtype}
          mode="create"
          onClose={() => setCreating(false)}
          onSaved={(key) => {
            load();
            setSelectedKey(key);
          }}
        />
      ) : null}

      {bulkReport ? (
        <Modal title="KPI bulk upload report" onClose={() => setBulkReport(null)} wide>
          <div className="bulk-report">
            <div>
              <h4>Created ({bulkReport.created.length})</h4>
              {bulkReport.created.length === 0 ? (
                <span className="muted">none</span>
              ) : (
                bulkReport.created.map((k) => (
                  <span key={k} className="chip chip-green mono">
                    {k}
                  </span>
                ))
              )}
            </div>
            <div>
              <h4>Updated ({bulkReport.updated.length})</h4>
              {bulkReport.updated.length === 0 ? (
                <span className="muted">none</span>
              ) : (
                bulkReport.updated.map((k) => (
                  <span key={k} className="chip chip-blue mono">
                    {k}
                  </span>
                ))
              )}
            </div>
            <div>
              <h4>Errors ({bulkReport.errors.length})</h4>
              {bulkReport.errors.length === 0 ? (
                <span className="muted">none</span>
              ) : (
                <ul className="error-list">
                  {bulkReport.errors.map((e, i) => (
                    <li key={i}>
                      <span className="chip chip-red">row {e.row}</span> {e.message}
                    </li>
                  ))}
                </ul>
              )}
            </div>
            <p className="hint">Bulk-created versions land as drafts — submit and approve them via maker-checker as usual.</p>
          </div>
        </Modal>
      ) : null}
    </div>
  );
}

export function MastersPage() {
  const { tab = 'prompts' } = useParams();
  const navigate = useNavigate();

  if (!TABS.some((t) => t.id === tab)) {
    return <Navigate to="/admin/masters/prompts" replace />;
  }

  return (
    <div className="page page-wide">
      <div className="page-head">
        <h1>Masters workbench</h1>
      </div>
      <div className="tabs">
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            className={`tab${tab === t.id ? ' active' : ''}`}
            onClick={() => navigate(`/admin/masters/${t.id}`)}
          >
            {t.label}
          </button>
        ))}
      </div>
      {tab === 'settings' ? <SettingsTab /> : <MasterWorkbench mtype={tab as MasterType} key={tab} />}
    </div>
  );
}
