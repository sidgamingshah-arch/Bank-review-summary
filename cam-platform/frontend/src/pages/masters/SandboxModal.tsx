import { useState } from 'react';
import { api, errorMessage } from '../../api/client';
import type { SandboxResult } from '../../api/types';
import { Markdown } from '../../components/Markdown';
import { Modal } from '../../components/Modal';
import { useToast } from '../../components/Toast';

interface Props {
  promptKey: string;
  onClose: () => void;
}

interface SampleDoc {
  doctype_code: string;
  text: string;
}

export function SandboxModal({ promptKey, onClose }: Props) {
  const toast = useToast();
  const [docs, setDocs] = useState<SampleDoc[]>([{ doctype_code: '', text: '' }]);
  const [result, setResult] = useState<SandboxResult | null>(null);
  const [busy, setBusy] = useState(false);

  const setDoc = (idx: number, patch: Partial<SampleDoc>) => {
    setDocs((cur) => cur.map((d, i) => (i === idx ? { ...d, ...patch } : d)));
  };

  const run = async () => {
    setBusy(true);
    setResult(null);
    try {
      const sample_docs = docs.filter((d) => d.doctype_code.trim() && d.text.trim());
      const res = await api.post<SandboxResult>(`/api/masters/prompts/${encodeURIComponent(promptKey)}/sandbox-test`, {
        sample_docs,
      });
      setResult(res);
    } catch (err) {
      toast.error(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      title={`Sandbox test — ${promptKey}`}
      onClose={onClose}
      wide
      footer={
        <>
          <button type="button" className="btn" onClick={onClose}>
            Close
          </button>
          <button
            type="button"
            className="btn btn-primary"
            disabled={busy || !docs.some((d) => d.doctype_code.trim() && d.text.trim())}
            onClick={run}
          >
            {busy ? 'Testing…' : 'Run sandbox test'}
          </button>
        </>
      }
    >
      <p className="hint">Tests the latest DRAFT version of this prompt against pasted sample document text (nothing is stored).</p>
      {docs.map((d, idx) => (
        <div key={idx} className="sample-doc">
          <div className="sample-doc-head">
            <input
              className="input mono"
              placeholder="doctype_code"
              value={d.doctype_code}
              onChange={(e) => setDoc(idx, { doctype_code: e.target.value })}
            />
            {docs.length > 1 ? (
              <button type="button" className="btn btn-sm btn-danger" onClick={() => setDocs(docs.filter((_, i) => i !== idx))}>
                Remove
              </button>
            ) : null}
          </div>
          <textarea
            className="textarea mono"
            placeholder="Paste sample document text…"
            value={d.text}
            onChange={(e) => setDoc(idx, { text: e.target.value })}
          />
        </div>
      ))}
      <button type="button" className="btn btn-sm" onClick={() => setDocs([...docs, { doctype_code: '', text: '' }])}>
        + Add sample document
      </button>

      {result ? (
        <div className="sandbox-result">
          <div className="card-head">
            <h4>Result</h4>
            <span className="muted mono">
              {result.model}
              {result.usage ? ` · ${result.usage.input_tokens ?? '?'} in / ${result.usage.output_tokens ?? '?'} out` : ''}
            </span>
          </div>
          <Markdown content={result.content} />
        </div>
      ) : null}
    </Modal>
  );
}
