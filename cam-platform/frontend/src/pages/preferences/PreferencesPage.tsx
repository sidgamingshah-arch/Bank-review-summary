import { useEffect, useState } from 'react';
import { api, errorMessage } from '../../api/client';
import type { PreferenceProfile, PreferenceProfileInput } from '../../api/types';
import { useAuth } from '../../auth/AuthContext';
import { PreferenceForm } from '../../components/PreferenceForm';
import { PageLoading } from '../../components/Spinner';
import { useToast } from '../../components/Toast';

function stripScope(p: PreferenceProfile): PreferenceProfileInput {
  return { tonality: p.tonality, structure_bias: p.structure_bias, table_usage: p.table_usage, length: p.length };
}

interface EditorProps {
  title: string;
  hint: string;
  path: string;
  idPrefix: string;
}

function PreferenceEditor({ title, hint, path, idPrefix }: EditorProps) {
  const toast = useToast();
  const [value, setValue] = useState<PreferenceProfileInput | null>(null);
  const [meta, setMeta] = useState<PreferenceProfile | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api
      .get<PreferenceProfile>(path)
      .then((p) => {
        if (!cancelled) {
          setValue(stripScope(p));
          setMeta(p);
        }
      })
      .catch((err) => toast.error(errorMessage(err)));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path]);

  const save = async () => {
    if (!value) return;
    setSaving(true);
    try {
      const updated = await api.put<PreferenceProfile>(path, value);
      setMeta(updated);
      setValue(stripScope(updated));
      toast.success(`${title} saved`);
    } catch (err) {
      toast.error(errorMessage(err));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="card">
      <div className="card-head">
        <h2>{title}</h2>
        {meta ? <span className="muted">scope: {meta.scope} · updated {new Date(meta.updated_at).toLocaleString()}</span> : null}
      </div>
      <p className="muted">{hint}</p>
      {value ? (
        <>
          <PreferenceForm value={value} onChange={setValue} idPrefix={idPrefix} />
          <div className="actions-row">
            <button type="button" className="btn btn-primary" onClick={save} disabled={saving}>
              {saving ? 'Saving…' : 'Save'}
            </button>
          </div>
        </>
      ) : (
        <PageLoading />
      )}
    </div>
  );
}

export function PreferencesPage() {
  const { hasRole } = useAuth();
  return (
    <div className="page">
      <div className="page-head">
        <h1>Output preferences</h1>
      </div>
      <PreferenceEditor
        title="My preferences"
        hint="Applied to every generation you start (unless you override per run). Ignored for fixed-format sections."
        path="/api/auth/preferences"
        idPrefix="own"
      />
      {hasRole('business_admin') ? (
        <PreferenceEditor
          title="Organisation default"
          hint="Fallback profile for users who have not set their own preferences."
          path="/api/auth/preferences/org-default"
          idPrefix="org"
        />
      ) : null}
    </div>
  );
}
