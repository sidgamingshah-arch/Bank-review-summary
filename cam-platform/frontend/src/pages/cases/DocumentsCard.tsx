import { useRef, useState } from 'react';
import type { DragEvent } from 'react';
import { api, errorMessage } from '../../api/client';
import { uploadCaseDocument } from '../../api/uploads';
import type { CaseDocument, Tag } from '../../api/types';
import { ConfirmDialog } from '../../components/ConfirmDialog';
import { EmptyState } from '../../components/EmptyState';
import { Modal } from '../../components/Modal';
import { Spinner } from '../../components/Spinner';
import { StatusChip } from '../../components/StatusChip';
import { useToast } from '../../components/Toast';

interface Props {
  caseId: string;
  documents: CaseDocument[];
  doctypes: string[];
  onReload: () => Promise<void>;
}

type UploadState = 'pending' | 'uploading' | 'done' | 'quarantined' | 'error';

interface UploadRow {
  id: number;
  name: string;
  state: UploadState;
  note?: string;
}

interface TagFormState {
  docId: string;
  tag: Tag | null; // null = add new
  doctype_code: string;
  period_label: string;
  seq_order: string;
}

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

export function DocumentsCard({ caseId, documents, doctypes, onReload }: Props) {
  const toast = useToast();
  const fileInput = useRef<HTMLInputElement>(null);
  const [dragOver, setDragOver] = useState(false);
  const [uploads, setUploads] = useState<UploadRow[]>([]);
  const [uploading, setUploading] = useState(false);
  const [showPull, setShowPull] = useState(false);
  const [externalRef, setExternalRef] = useState('');
  const [pulling, setPulling] = useState(false);
  const [deleteDoc, setDeleteDoc] = useState<CaseDocument | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [tagForm, setTagForm] = useState<TagFormState | null>(null);
  const [tagBusy, setTagBusy] = useState(false);

  const setUploadState = (id: number, state: UploadState, note?: string) => {
    setUploads((cur) => cur.map((u) => (u.id === id ? { ...u, state, note } : u)));
  };

  /** Fans a multi-select out into sequential one-file-per-request uploads (contract FR-C02). */
  const uploadFiles = async (files: File[]) => {
    if (files.length === 0 || uploading) return;
    setUploading(true);
    const base = Date.now();
    const rows: UploadRow[] = files.map((f, i) => ({ id: base + i, name: f.name, state: 'pending' }));
    setUploads(rows);
    for (let i = 0; i < files.length; i++) {
      const rowId = base + i;
      setUploadState(rowId, 'uploading');
      try {
        const doc = await uploadCaseDocument(caseId, files[i], 'upload');
        if (doc.status === 'quarantined') {
          setUploadState(rowId, 'quarantined', doc.quarantine_reason ?? 'quarantined');
        } else {
          setUploadState(rowId, 'done', doc.duplicate_of ? 'duplicate of an existing document' : undefined);
        }
      } catch (err) {
        setUploadState(rowId, 'error', errorMessage(err));
      }
    }
    setUploading(false);
    await onReload();
  };

  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    uploadFiles(Array.from(e.dataTransfer.files));
  };

  const pull = async () => {
    setPulling(true);
    try {
      const doc = await api.post<CaseDocument>(`/api/cases/${caseId}/pull`, { source: 'repository', external_ref: externalRef.trim() });
      if (doc.status === 'quarantined') {
        toast.error(`Pulled document was quarantined: ${doc.quarantine_reason ?? 'unknown reason'}`);
      } else {
        toast.success(`Pulled ${doc.filename} from repository`);
      }
      setShowPull(false);
      setExternalRef('');
      await onReload();
    } catch (err) {
      toast.error(errorMessage(err));
    } finally {
      setPulling(false);
    }
  };

  const removeDocument = async () => {
    if (!deleteDoc) return;
    setDeleting(true);
    try {
      await api.del(`/api/documents/${deleteDoc.id}`);
      toast.success(`Deleted ${deleteDoc.filename}`);
      setDeleteDoc(null);
      await onReload();
    } catch (err) {
      toast.error(errorMessage(err));
    } finally {
      setDeleting(false);
    }
  };

  const confirmTag = async (doc: CaseDocument, tag: Tag) => {
    try {
      await api.patch<Tag>(`/api/documents/${doc.id}/tags/${tag.id}`, { confirmed: true });
      toast.success(`Tag ${tag.doctype_code} confirmed`);
      await onReload();
    } catch (err) {
      toast.error(errorMessage(err));
    }
  };

  const removeTag = async (doc: CaseDocument, tag: Tag) => {
    try {
      await api.del(`/api/documents/${doc.id}/tags/${tag.id}`);
      toast.success(`Tag ${tag.doctype_code} removed`);
      await onReload();
    } catch (err) {
      toast.error(errorMessage(err));
    }
  };

  const saveTag = async () => {
    if (!tagForm) return;
    setTagBusy(true);
    const body: Record<string, unknown> = {
      doctype_code: tagForm.doctype_code,
      period_label: tagForm.period_label.trim() || null,
      seq_order: tagForm.seq_order.trim() === '' ? null : Number(tagForm.seq_order),
    };
    try {
      if (tagForm.tag) {
        await api.patch<Tag>(`/api/documents/${tagForm.docId}/tags/${tagForm.tag.id}`, body);
        toast.success('Tag updated');
      } else {
        await api.post<Tag>(`/api/documents/${tagForm.docId}/tags`, body);
        toast.success('Tag added');
      }
      setTagForm(null);
      await onReload();
    } catch (err) {
      toast.error(errorMessage(err));
    } finally {
      setTagBusy(false);
    }
  };

  const openTagForm = (doc: CaseDocument, tag: Tag | null) => {
    setTagForm({
      docId: doc.id,
      tag,
      doctype_code: tag?.doctype_code ?? (doctypes[0] ?? ''),
      period_label: tag?.period_label ?? '',
      seq_order: tag?.seq_order != null ? String(tag.seq_order) : '',
    });
  };

  return (
    <div className="card">
      <div className="card-head">
        <h2>Documents</h2>
        <div className="btn-row">
          <button type="button" className="btn btn-sm" onClick={() => setShowPull(true)}>
            Pull from repository
          </button>
        </div>
      </div>

      <div
        className={`dropzone${dragOver ? ' dropzone-active' : ''}`}
        onDragOver={(e) => {
          e.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={onDrop}
        onClick={() => fileInput.current?.click()}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => e.key === 'Enter' && fileInput.current?.click()}
      >
        <strong>Drop files here</strong> or click to browse
        <div className="hint">.pdf .docx .xlsx .csv .txt · max 25 MB · each file is uploaded as its own request</div>
        <input
          ref={fileInput}
          type="file"
          multiple
          hidden
          accept=".pdf,.docx,.xlsx,.csv,.txt"
          onChange={(e) => {
            uploadFiles(Array.from(e.target.files ?? []));
            e.target.value = '';
          }}
        />
      </div>

      {uploads.length > 0 ? (
        <div className="upload-list">
          {uploads.map((u) => (
            <div key={u.id} className="upload-row">
              <span className="upload-name">{u.name}</span>
              {u.state === 'uploading' ? <Spinner small /> : null}
              <StatusChip
                status={u.state === 'error' ? 'failed' : u.state}
                label={u.state === 'done' ? 'uploaded' : u.state}
              />
              {u.note ? <span className="upload-note">{u.note}</span> : null}
            </div>
          ))}
        </div>
      ) : null}

      {documents.length === 0 ? (
        <EmptyState title="No documents yet" hint="Upload borrower documents or pull them from the repository." />
      ) : (
        <div className="doc-list">
          {documents.map((doc) => (
            <div key={doc.id} className={`doc-row${doc.status === 'quarantined' ? ' doc-quarantined' : ''}`}>
              <div className="doc-main">
                <div className="doc-title">
                  <strong>{doc.filename}</strong>
                  <span className="muted">{formatBytes(doc.size_bytes)}</span>
                  <StatusChip status={doc.status} />
                  <StatusChip status={doc.extraction} label={`extraction: ${doc.extraction}`} />
                  {doc.origin !== 'upload' ? <span className="chip chip-gray">{doc.origin}</span> : null}
                  {doc.duplicate_of ? (
                    <span className="chip chip-amber" title={`Duplicate of document ${doc.duplicate_of}`}>
                      duplicate
                    </span>
                  ) : null}
                </div>
                {doc.status === 'quarantined' ? (
                  <div className="banner banner-error slim">Quarantined: {doc.quarantine_reason ?? 'unknown reason'}</div>
                ) : (
                  <div className="tag-list">
                    {doc.tags.map((tag) => (
                      <div key={tag.id} className="tag-row">
                        <span className="chip chip-blue mono">{tag.doctype_code}</span>
                        {tag.source === 'auto' && tag.confidence != null ? (
                          <span className="muted">{Math.round(tag.confidence * 100)}%</span>
                        ) : null}
                        <StatusChip status={tag.source} />
                        {tag.needs_review ? <span className="chip chip-amber">needs review</span> : null}
                        {tag.period_label ? <span className="chip chip-gray">{tag.period_label}</span> : null}
                        {tag.seq_order != null ? <span className="muted">#{tag.seq_order}</span> : null}
                        <span className="btn-row tag-actions">
                          {tag.needs_review || (tag.source === 'auto' && !tag.confirmed) ? (
                            <button type="button" className="btn btn-sm" onClick={() => confirmTag(doc, tag)}>
                              Confirm
                            </button>
                          ) : null}
                          <button type="button" className="btn btn-sm" onClick={() => openTagForm(doc, tag)}>
                            Edit
                          </button>
                          <button type="button" className="btn btn-sm btn-danger" onClick={() => removeTag(doc, tag)}>
                            Remove
                          </button>
                        </span>
                      </div>
                    ))}
                    <button type="button" className="btn btn-sm btn-ghost" onClick={() => openTagForm(doc, null)}>
                      + Add tag
                    </button>
                  </div>
                )}
              </div>
              <div className="doc-side">
                <button type="button" className="btn btn-sm btn-danger" onClick={() => setDeleteDoc(doc)}>
                  Delete
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {showPull ? (
        <Modal
          title="Pull document from repository"
          onClose={() => setShowPull(false)}
          footer={
            <>
              <button type="button" className="btn" onClick={() => setShowPull(false)}>
                Cancel
              </button>
              <button type="button" className="btn btn-primary" disabled={pulling || !externalRef.trim()} onClick={pull}>
                {pulling ? 'Pulling…' : 'Pull document'}
              </button>
            </>
          }
        >
          <div className="field">
            <label>External reference</label>
            <input
              className="input"
              value={externalRef}
              placeholder="e.g. repo://borrower/annual-report-2025"
              onChange={(e) => setExternalRef(e.target.value)}
              autoFocus
            />
            <div className="hint">The document is fetched from the bank repository and passed through the same intake pipeline.</div>
          </div>
        </Modal>
      ) : null}

      {deleteDoc ? (
        <ConfirmDialog
          title="Delete document"
          message={`Delete "${deleteDoc.filename}" and its tags? This cannot be undone.`}
          confirmLabel="Delete"
          danger
          busy={deleting}
          onConfirm={removeDocument}
          onCancel={() => setDeleteDoc(null)}
        />
      ) : null}

      {tagForm ? (
        <Modal
          title={tagForm.tag ? 'Edit tag' : 'Add tag'}
          onClose={() => setTagForm(null)}
          footer={
            <>
              <button type="button" className="btn" onClick={() => setTagForm(null)}>
                Cancel
              </button>
              <button type="button" className="btn btn-primary" disabled={tagBusy || !tagForm.doctype_code} onClick={saveTag}>
                {tagBusy ? 'Saving…' : 'Save tag'}
              </button>
            </>
          }
        >
          <div className="field">
            <label>Document type</label>
            {doctypes.length > 0 ? (
              <select className="select" value={tagForm.doctype_code} onChange={(e) => setTagForm({ ...tagForm, doctype_code: e.target.value })}>
                {!doctypes.includes(tagForm.doctype_code) && tagForm.doctype_code ? (
                  <option value={tagForm.doctype_code}>{tagForm.doctype_code} (unpublished)</option>
                ) : null}
                {doctypes.map((d) => (
                  <option key={d} value={d}>
                    {d}
                  </option>
                ))}
              </select>
            ) : (
              <input
                className="input"
                value={tagForm.doctype_code}
                placeholder="doctype_code"
                onChange={(e) => setTagForm({ ...tagForm, doctype_code: e.target.value })}
              />
            )}
          </div>
          <div className="form-grid-2">
            <div className="field">
              <label>Period label</label>
              <input
                className="input"
                value={tagForm.period_label}
                placeholder="e.g. FY2025"
                onChange={(e) => setTagForm({ ...tagForm, period_label: e.target.value })}
              />
            </div>
            <div className="field">
              <label>Sequence order</label>
              <input
                className="input"
                type="number"
                value={tagForm.seq_order}
                onChange={(e) => setTagForm({ ...tagForm, seq_order: e.target.value })}
              />
            </div>
          </div>
        </Modal>
      ) : null}
    </div>
  );
}
