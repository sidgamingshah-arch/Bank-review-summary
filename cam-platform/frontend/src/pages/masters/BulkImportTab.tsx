import { useRef, useState } from 'react';
import { api, errorMessage } from '../../api/client';
import { uploadMastersBulk } from '../../api/uploads';
import type { MastersBulkReport } from '../../api/types';
import { useToast } from '../../components/Toast';

export function BulkImportTab() {
  const toast = useToast();
  const fileInput = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [report, setReport] = useState<MastersBulkReport | null>(null);

  const downloadTemplate = async () => {
    try {
      await api.download('/api/masters/bulk-template', 'cam-masters-template.xlsx');
    } catch (err) {
      toast.error(errorMessage(err));
    }
  };

  const upload = async (file: File) => {
    setBusy(true);
    setReport(null);
    try {
      const result = await uploadMastersBulk(file);
      setReport(result);
      const failed = result.errors.length;
      if (failed) {
        toast.info(`Uploaded with ${failed} row error${failed === 1 ? '' : 's'} — see report`);
      } else {
        toast.success('Workbook imported as drafts');
      }
    } catch (err) {
      toast.error(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  const errorWhere = (e: MastersBulkReport['errors'][number]): string =>
    e.entry ?? `${e.sheet ?? '?'} row ${e.row ?? '?'}`;

  return (
    <div className="card settings-card">
      <div className="card-head">
        <h2>Bulk import masters</h2>
      </div>

      <p className="hint">
        Download the Excel template, fill one row per entry across the sheets (document types,
        industries, prompts, KPI sets, templates), then upload it. Dependency order is handled
        for you. Every entry lands as a <strong>draft</strong> — submit and approve via
        maker-checker before it takes effect.
      </p>

      <div className="btn-row">
        <button type="button" className="btn" onClick={downloadTemplate}>
          Download template (.xlsx)
        </button>
        <button
          type="button"
          className="btn btn-primary"
          disabled={busy}
          onClick={() => fileInput.current?.click()}
        >
          {busy ? 'Uploading…' : 'Upload filled workbook'}
        </button>
        <input
          ref={fileInput}
          type="file"
          accept=".xlsx"
          hidden
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) upload(f);
            e.target.value = '';
          }}
        />
      </div>

      {report ? (
        <div className="bulk-report" style={{ marginTop: '1.25rem' }}>
          <div>
            <h4>Created ({report.created.length})</h4>
            {report.created.length === 0 ? (
              <span className="muted">none</span>
            ) : (
              report.created.map((c) => (
                <span key={c.entry} className="chip chip-green mono">
                  {c.entry} v{c.version_no}
                </span>
              ))
            )}
          </div>
          <div>
            <h4>Updated ({report.updated.length})</h4>
            {report.updated.length === 0 ? (
              <span className="muted">none</span>
            ) : (
              report.updated.map((c) => (
                <span key={c.entry} className="chip chip-blue mono">
                  {c.entry} v{c.version_no}
                </span>
              ))
            )}
          </div>
          <div>
            <h4>Unchanged ({report.unchanged.length})</h4>
            {report.unchanged.length === 0 ? (
              <span className="muted">none</span>
            ) : (
              report.unchanged.map((k) => (
                <span key={k} className="chip chip-gray mono">
                  {k}
                </span>
              ))
            )}
          </div>
          <div>
            <h4>Errors ({report.errors.length})</h4>
            {report.errors.length === 0 ? (
              <span className="muted">none</span>
            ) : (
              <ul className="error-list">
                {report.errors.map((e, i) => (
                  <li key={i}>
                    <span className="chip chip-red mono">{errorWhere(e)}</span> {e.message}
                  </li>
                ))}
              </ul>
            )}
          </div>
          <p className="hint">{report.note}</p>
        </div>
      ) : null}
    </div>
  );
}
