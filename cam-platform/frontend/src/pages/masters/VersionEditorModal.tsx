import { useState } from 'react';
import { api, ApiError, errorMessage } from '../../api/client';
import type {
  DoctypePayload,
  IndustryPayload,
  KpiSetPayload,
  MasterPayload,
  MasterType,
  PromptPayload,
  TemplatePayload,
} from '../../api/types';
import { Modal } from '../../components/Modal';
import { useToast } from '../../components/Toast';
import { PromptForm } from './forms/PromptForm';
import { TemplateForm } from './forms/TemplateForm';
import { DoctypeForm } from './forms/DoctypeForm';
import { IndustryForm } from './forms/IndustryForm';
import { KpiSetForm } from './forms/KpiSetForm';

interface Props {
  mtype: MasterType;
  mode: 'create' | 'newVersion';
  itemKey?: string;
  initialPayload?: MasterPayload;
  onClose: () => void;
  onSaved: (key: string) => void;
}

function emptyPayload(mtype: MasterType): MasterPayload {
  switch (mtype) {
    case 'prompts':
      return {
        section_code: '',
        section_name: '',
        scope: 'section',
        prompt_text: '',
        source_doc_types: [],
        uses_industry_kpis: false,
      } satisfies PromptPayload;
    case 'templates':
      return {
        name: '',
        segment: 'corporate',
        relationship: 'etb',
        template_instructions: '',
        sections: [],
        required_doc_types: [],
      } satisfies TemplatePayload;
    case 'doctypes':
      return {
        code: '',
        name: '',
        description: '',
        synonyms: [],
        keywords: [],
        active: true,
      } satisfies DoctypePayload;
    case 'industries':
      return { sector_code: '', sector_name: '', industry_code: '', industry_name: '' } satisfies IndustryPayload;
    case 'kpi-sets':
      return { industry_code: '', kpis: [] } satisfies KpiSetPayload;
  }
}

function deriveKey(mtype: MasterType, payload: MasterPayload, slugDraft: string): string {
  switch (mtype) {
    case 'prompts': {
      const p = payload as PromptPayload;
      return p.scope === 'global' ? 'global_standing_rules' : p.section_code.trim();
    }
    case 'templates':
      return slugDraft.trim();
    case 'doctypes':
      return (payload as DoctypePayload).code.trim();
    case 'industries':
      return (payload as IndustryPayload).industry_code.trim();
    case 'kpi-sets':
      return (payload as KpiSetPayload).industry_code.trim();
  }
}

const TITLES: Record<MasterType, string> = {
  prompts: 'Prompt',
  templates: 'Template',
  doctypes: 'Document type',
  industries: 'Industry',
  'kpi-sets': 'KPI set',
};

export function VersionEditorModal({ mtype, mode, itemKey, initialPayload, onClose, onSaved }: Props) {
  const toast = useToast();
  const isNew = mode === 'create';
  const [payload, setPayload] = useState<MasterPayload>(() => initialPayload ?? emptyPayload(mtype));
  const [slugDraft, setSlugDraft] = useState(itemKey ?? '');
  const [changeNote, setChangeNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<{ message: string; details: unknown } | null>(null);

  const key = isNew ? deriveKey(mtype, payload, slugDraft) : itemKey ?? '';

  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      if (isNew) {
        await api.post(`/api/masters/${mtype}`, { key, payload, change_note: changeNote.trim() });
        toast.success(`${TITLES[mtype]} "${key}" created (draft v1)`);
      } else {
        await api.post(`/api/masters/${mtype}/${encodeURIComponent(key)}/versions`, {
          payload,
          change_note: changeNote.trim(),
        });
        toast.success(`New draft version created for "${key}"`);
      }
      onSaved(key);
      onClose();
    } catch (err) {
      if (err instanceof ApiError) {
        setError({ message: `${err.message} (${err.code})`, details: err.details });
      } else {
        setError({ message: errorMessage(err), details: null });
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      title={isNew ? `New ${TITLES[mtype].toLowerCase()}` : `New version — ${itemKey}`}
      onClose={onClose}
      wide
      footer={
        <>
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="btn btn-primary" disabled={busy || !key || !changeNote.trim()} onClick={save}>
            {busy ? 'Saving…' : isNew ? 'Create draft v1' : 'Save new draft'}
          </button>
        </>
      }
    >
      {isNew && mtype === 'templates' ? (
        <div className="field">
          <label>Template key (slug)</label>
          <input className="input mono" value={slugDraft} placeholder="e.g. corporate-etb-standard" onChange={(e) => setSlugDraft(e.target.value)} />
        </div>
      ) : null}

      {mtype === 'prompts' ? (
        <PromptForm value={payload as PromptPayload} onChange={setPayload} isNew={isNew} />
      ) : mtype === 'templates' ? (
        <TemplateForm value={payload as TemplatePayload} onChange={setPayload} />
      ) : mtype === 'doctypes' ? (
        <DoctypeForm value={payload as DoctypePayload} onChange={setPayload} isNew={isNew} />
      ) : mtype === 'industries' ? (
        <IndustryForm value={payload as IndustryPayload} onChange={setPayload} isNew={isNew} />
      ) : (
        <KpiSetForm value={payload as KpiSetPayload} onChange={setPayload} isNew={isNew} />
      )}

      <div className="field change-note">
        <label>
          Change note <span className="req">required</span>
        </label>
        <input
          className="input"
          value={changeNote}
          placeholder="Why is this change being made?"
          onChange={(e) => setChangeNote(e.target.value)}
        />
      </div>

      {error ? (
        <div className="banner banner-error">
          {error.message}
          {error.details != null ? <pre className="detail-json">{JSON.stringify(error.details, null, 2)}</pre> : null}
        </div>
      ) : null}
    </Modal>
  );
}
