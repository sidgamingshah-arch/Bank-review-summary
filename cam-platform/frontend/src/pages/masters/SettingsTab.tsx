import { useEffect, useState } from 'react';
import { api, errorMessage } from '../../api/client';
import type { MasterSettings } from '../../api/types';
import { PageLoading } from '../../components/Spinner';
import { useToast } from '../../components/Toast';

export function SettingsTab() {
  const toast = useToast();
  const [settings, setSettings] = useState<MasterSettings | null>(null);
  const [threshold, setThreshold] = useState('');
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api
      .get<MasterSettings>('/api/masters/settings')
      .then((s) => {
        if (!cancelled) {
          setSettings(s);
          setThreshold(String(s.tagging_confidence_threshold));
        }
      })
      .catch((err) => toast.error(errorMessage(err)));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const save = async () => {
    if (!settings) return;
    const value = Number(threshold);
    if (Number.isNaN(value) || value < 0 || value > 1) {
      toast.error('Threshold must be a number between 0 and 1');
      return;
    }
    setSaving(true);
    try {
      // Other settings keys are passed through unchanged.
      const updated = await api.put<MasterSettings>('/api/masters/settings', {
        ...settings,
        tagging_confidence_threshold: value,
      });
      setSettings(updated);
      setThreshold(String(updated.tagging_confidence_threshold));
      toast.success('Settings saved');
    } catch (err) {
      toast.error(errorMessage(err));
    } finally {
      setSaving(false);
    }
  };

  if (!settings) return <PageLoading label="Loading settings…" />;

  return (
    <div className="card settings-card">
      <div className="card-head">
        <h2>Platform settings</h2>
      </div>
      <div className="field">
        <label>Tagging confidence threshold</label>
        <input
          className="input slim"
          type="number"
          min={0}
          max={1}
          step={0.05}
          value={threshold}
          onChange={(e) => setThreshold(e.target.value)}
        />
        <div className="hint">
          Auto-tags with confidence below this value are flagged “needs review” on the case workspace (0–1).
        </div>
      </div>
      <div className="actions-row">
        <button type="button" className="btn btn-primary" disabled={saving} onClick={save}>
          {saving ? 'Saving…' : 'Save settings'}
        </button>
      </div>
    </div>
  );
}
