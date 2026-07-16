import { useEffect, useRef, useState } from 'react';
import { api, errorMessage } from '../../api/client';
import { uploadCaseDocument } from '../../api/uploads';
import type { Cam, CamSection, ChatMessage, ChatResponse, ChatScope, Suggestion, SuggestionDecision } from '../../api/types';
import { Markdown } from '../../components/Markdown';
import { Spinner } from '../../components/Spinner';
import { useToast } from '../../components/Toast';
import { SuggestionCard } from './SuggestionCard';

interface Props {
  cam: Cam;
  activeSection: CamSection | null;
  enabled: boolean;
  onCamReload: () => Promise<Cam | null>;
}

export function ChatPanel({ cam, activeSection, enabled, onCamReload }: Props) {
  const toast = useToast();
  const fileInput = useRef<HTMLInputElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const [scope, setScope] = useState<ChatScope>('document');
  const [messages, setMessages] = useState<ChatMessage[] | null>(null);
  const [pending, setPending] = useState<Suggestion[]>([]);
  const [pendingOpen, setPendingOpen] = useState(false);
  const [input, setInput] = useState('');
  const [attachments, setAttachments] = useState<File[]>([]);
  const [sending, setSending] = useState(false);
  const [inlineSuggestions, setInlineSuggestions] = useState<Suggestion[]>([]);
  const [deciding, setDeciding] = useState(false);

  const sectionId = scope === 'section' ? activeSection?.id ?? null : null;

  const sectionName = (sid: string | null): string | undefined =>
    sid ? cam.sections.find((s) => s.id === sid)?.name : undefined;

  const loadMessages = async () => {
    try {
      const qs = sectionId ? `?section_id=${encodeURIComponent(sectionId)}` : '';
      setMessages(await api.get<ChatMessage[]>(`/api/cams/${cam.id}/chat${qs}`));
    } catch (err) {
      toast.error(errorMessage(err));
      setMessages([]);
    }
  };

  const loadPending = async () => {
    try {
      setPending(await api.get<Suggestion[]>(`/api/cams/${cam.id}/suggestions?status=pending`));
    } catch {
      setPending([]);
    }
  };

  useEffect(() => {
    setMessages(null);
    setInlineSuggestions([]);
    loadMessages();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cam.id, scope, sectionId]);

  useEffect(() => {
    loadPending();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cam.id]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages, inlineSuggestions]);

  const send = async () => {
    const message = input.trim();
    if (!message || sending) return;
    setSending(true);
    try {
      // Attachments become CASE documents first (one file per request, origin 'chat'),
      // then their ids ride along on the chat message.
      const attachedIds: string[] = [];
      for (const file of attachments) {
        const doc = await uploadCaseDocument(cam.case_id, file, 'chat');
        if (doc.status === 'quarantined') {
          toast.error(`Attachment "${file.name}" was quarantined: ${doc.quarantine_reason ?? 'unknown reason'}`);
        } else {
          attachedIds.push(doc.id);
        }
      }
      const body: Record<string, unknown> = { scope, message };
      if (sectionId) body.section_id = sectionId;
      if (attachedIds.length > 0) body.attached_document_ids = attachedIds;
      const res = await api.post<ChatResponse>(`/api/cams/${cam.id}/chat`, body);
      setMessages((cur) => [...(cur ?? []), res.message, res.reply]);
      if (res.suggestion) {
        setInlineSuggestions((cur) => [...cur, res.suggestion as Suggestion]);
        loadPending();
      }
      setInput('');
      setAttachments([]);
    } catch (err) {
      toast.error(errorMessage(err));
    } finally {
      setSending(false);
    }
  };

  const decide = async (s: Suggestion, decision: 'accept' | 'reject') => {
    setDeciding(true);
    try {
      if (decision === 'accept') {
        await api.post<SuggestionDecision>(`/api/cams/${cam.id}/suggestions/${s.id}/accept`);
        toast.success('Suggestion accepted — section updated');
        await onCamReload();
      } else {
        await api.post<SuggestionDecision>(`/api/cams/${cam.id}/suggestions/${s.id}/reject`, {});
        toast.info('Suggestion rejected');
      }
      setInlineSuggestions((cur) => cur.filter((x) => x.id !== s.id));
      loadPending();
    } catch (err) {
      toast.error(errorMessage(err));
    } finally {
      setDeciding(false);
    }
  };

  return (
    <div className="chat-panel">
      <div className="chat-head">
        <h3>AI assistant</h3>
        <div className="scope-toggle" role="tablist" aria-label="Chat scope">
          <button
            type="button"
            className={`scope-btn${scope === 'document' ? ' active' : ''}`}
            onClick={() => setScope('document')}
          >
            Whole document
          </button>
          <button
            type="button"
            className={`scope-btn${scope === 'section' ? ' active' : ''}`}
            onClick={() => setScope('section')}
            disabled={!activeSection}
            title={activeSection ? `Scope: ${activeSection.name}` : 'Select a section first'}
          >
            This section
          </button>
        </div>
        {scope === 'section' && activeSection ? <div className="hint">Scoped to: {activeSection.name}</div> : null}
      </div>

      {pending.length > 0 ? (
        <div className="pending-strip">
          <button type="button" className="collapsible-toggle" onClick={() => setPendingOpen(!pendingOpen)}>
            {pendingOpen ? '▾' : '▸'} Pending suggestions ({pending.length})
          </button>
          {pendingOpen
            ? pending.map((s) => (
                <SuggestionCard
                  key={s.id}
                  suggestion={s}
                  sectionName={sectionName(s.section_id)}
                  busy={deciding}
                  enabled={enabled}
                  onAccept={(x) => decide(x, 'accept')}
                  onReject={(x) => decide(x, 'reject')}
                />
              ))
            : null}
        </div>
      ) : null}

      <div className="chat-messages" ref={scrollRef}>
        {messages === null ? (
          <Spinner label="Loading conversation…" />
        ) : messages.length === 0 && inlineSuggestions.length === 0 ? (
          <div className="muted chat-empty">
            Ask the assistant to tighten wording, restructure a section or explain a figure. Proposed changes always arrive as
            suggestions for you to accept or reject — they are never auto-applied.
          </div>
        ) : (
          <>
            {messages.map((m) => (
              <div key={m.id} className={`chat-msg chat-${m.role}`}>
                <div className="chat-msg-meta">
                  {m.role === 'user' ? 'You' : 'Assistant'}
                  {m.section_id ? <span className="muted"> · {sectionName(m.section_id) ?? 'section'}</span> : null}
                  {m.attached_document_ids.length > 0 ? (
                    <span className="muted"> · {m.attached_document_ids.length} attachment(s)</span>
                  ) : null}
                </div>
                {m.role === 'assistant' ? <Markdown content={m.content} /> : <div className="chat-user-text">{m.content}</div>}
              </div>
            ))}
            {inlineSuggestions.map((s) => (
              <SuggestionCard
                key={s.id}
                suggestion={s}
                sectionName={sectionName(s.section_id)}
                busy={deciding}
                enabled={enabled}
                onAccept={(x) => decide(x, 'accept')}
                onReject={(x) => decide(x, 'reject')}
              />
            ))}
          </>
        )}
        {sending ? <Spinner small label="Thinking…" /> : null}
      </div>

      <div className="chat-input-area">
        {attachments.length > 0 ? (
          <div className="chip-row">
            {attachments.map((f, i) => (
              <span key={`${f.name}-${i}`} className="chip-token">
                📎 {f.name}
                <button type="button" aria-label={`Remove ${f.name}`} onClick={() => setAttachments(attachments.filter((_, j) => j !== i))}>
                  ✕
                </button>
              </span>
            ))}
          </div>
        ) : null}
        <textarea
          className="textarea chat-input"
          placeholder={enabled ? 'Ask the assistant…' : 'CAM is final — conversation is read-only'}
          value={input}
          disabled={!enabled || sending}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault();
              send();
            }
          }}
        />
        <div className="chat-input-actions">
          <button
            type="button"
            className="btn btn-sm"
            disabled={!enabled || sending}
            onClick={() => fileInput.current?.click()}
            title="Attach files (uploaded to the case, origin: chat)"
          >
            📎 Attach
          </button>
          <input
            ref={fileInput}
            type="file"
            multiple
            hidden
            accept=".pdf,.docx,.xlsx,.csv,.txt"
            onChange={(e) => {
              setAttachments([...attachments, ...Array.from(e.target.files ?? [])]);
              e.target.value = '';
            }}
          />
          <button type="button" className="btn btn-sm btn-primary" disabled={!enabled || sending || !input.trim()} onClick={send}>
            {sending ? 'Sending…' : 'Send'}
          </button>
        </div>
      </div>
    </div>
  );
}
