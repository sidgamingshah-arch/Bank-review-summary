// TypeScript mirrors of cam-platform/docs/contracts.md (v1).

export type Role = 'business_admin' | 'it_admin' | 'analyst' | 'reviewer' | 'auditor';

export const ALL_ROLES: Role[] = ['business_admin', 'it_admin', 'analyst', 'reviewer', 'auditor'];

export interface User {
  id: string;
  username: string;
  display_name: string;
  email: string;
  roles: string[];
  active: boolean;
}

export interface TokenResponse {
  access_token: string;
  token_type: 'bearer';
  expires_in: number;
  user: User;
}

// ---- Preferences ----------------------------------------------------------

export type Tonality = 'crisp' | 'narrative';
export type StructureBias = 'bullets' | 'paragraphs';
export type TableUsage = 'auto' | 'prefer' | 'avoid';
export type LengthPref = 'concise' | 'standard' | 'detailed';

export interface PreferenceProfileInput {
  tonality: Tonality;
  structure_bias: StructureBias;
  table_usage: TableUsage;
  length: LengthPref;
}

export interface PreferenceProfile extends PreferenceProfileInput {
  scope: 'user' | 'org_default';
  updated_at: string;
}

export const DEFAULT_PREFERENCES: PreferenceProfileInput = {
  tonality: 'crisp',
  structure_bias: 'bullets',
  table_usage: 'auto',
  length: 'standard',
};

// ---- Masters ---------------------------------------------------------------

export type MasterType = 'prompts' | 'templates' | 'doctypes' | 'industries' | 'kpi-sets';

export type VersionStatus = 'draft' | 'in_review' | 'published' | 'retired' | 'rejected';

export interface ItemSummary {
  key: string;
  item_id: string;
  latest_version: number;
  published_version: number | null;
  updated_at: string;
}

export interface VersionMeta {
  version_no: number;
  status: VersionStatus;
  created_by: string;
  created_at: string;
  submitted_by?: string | null;
  approved_by?: string | null;
  approved_at?: string | null;
  effective_from?: string | null;
  change_note: string;
}

export interface Version extends VersionMeta {
  payload: MasterPayload;
}

export interface ItemDetail {
  key: string;
  item_id: string;
  versions: VersionMeta[];
  published_version: number | null;
}

export interface ModelOverrides {
  model?: string;
  temperature?: number;
  max_tokens?: number;
}

export interface PromptPayload {
  section_code: string;
  section_name: string;
  scope: 'section' | 'global';
  prompt_text: string;
  source_doc_types: string[];
  uses_industry_kpis: boolean;
  rendering_hints?: string;
  model_overrides?: ModelOverrides;
}

export interface TemplateSectionRow {
  order: number;
  section_code: string;
  mandatory: boolean;
  include_if_doctype?: string | null;
  length_guidance?: string;
  fixed_format: boolean;
}

export interface TemplatePayload {
  name: string;
  segment: 'corporate' | 'fi' | 'project_finance';
  relationship: 'etb' | 'ntb';
  template_instructions: string;
  sections: TemplateSectionRow[];
  required_doc_types: string[];
}

export interface FileConstraints {
  formats: string[];
  max_mb: number;
  max_count: number;
}

export interface DoctypePayload {
  code: string;
  name: string;
  description: string;
  synonyms: string[];
  keywords: string[];
  active: boolean;
  file_constraints?: FileConstraints | null;
  feeds_sections?: string[];
}

export interface IndustryPayload {
  sector_code: string;
  sector_name: string;
  industry_code: string;
  industry_name: string;
}

export interface KpiRow {
  code: string;
  name: string;
  definition: string;
  unit: string;
  polarity: 'higher_better' | 'lower_better';
  benchmark?: string | null;
  sections: string[];
}

export interface KpiSetPayload {
  industry_code: string;
  kpis: KpiRow[];
}

export type MasterPayload =
  | PromptPayload
  | TemplatePayload
  | DoctypePayload
  | IndustryPayload
  | KpiSetPayload;

export interface MasterSettings {
  tagging_confidence_threshold: number;
  [key: string]: unknown;
}

export interface KpiBulkReport {
  created: string[];
  updated: string[];
  errors: { row: number; message: string }[];
}

export interface SandboxResult {
  content: string;
  model: string;
  usage: { input_tokens?: number; output_tokens?: number } | null;
}

export interface DiffResult {
  diff: string;
}

// ---- Cases & documents -----------------------------------------------------

export type CaseSegment = 'corporate' | 'fi' | 'project_finance';
export type CaseRelationship = 'etb' | 'ntb';

export interface Case {
  id: string;
  borrower_name: string;
  segment: CaseSegment;
  relationship: CaseRelationship;
  industry_code: string;
  status: 'open' | 'generating' | 'drafted' | 'finalised';
  created_by: string;
  created_at: string;
}

export type DocumentStatus = 'quarantined' | 'ready' | 'no_text';
export type DocumentOrigin = 'upload' | 'chat' | 'repository';

export interface Tag {
  id: string;
  doctype_code: string;
  confidence: number | null;
  source: 'auto' | 'user';
  needs_review: boolean;
  period_label: string | null;
  seq_order: number | null;
  page_range: string | null;
  confirmed?: boolean;
}

export interface CaseDocument {
  id: string;
  case_id: string;
  filename: string;
  content_type: string;
  size_bytes: number;
  sha256: string;
  status: DocumentStatus;
  quarantine_reason: string | null;
  origin: DocumentOrigin;
  duplicate_of: string | null;
  extraction: 'ok' | 'empty' | 'unsupported';
  uploaded_by: string;
  uploaded_at: string;
  tags: Tag[];
}

export interface Completeness {
  required: string[];
  present: string[];
  missing: string[];
  can_proceed: boolean;
}

// ---- Runs ------------------------------------------------------------------

export type RunStatus = 'queued' | 'running' | 'complete' | 'partial' | 'failed';
export type RunSectionStatus = 'queued' | 'running' | 'complete' | 'failed' | 'skipped';

export interface AgentCheck {
  passed: boolean | null;
  omissions?: string[];
  inconsistencies?: string[];
  flags?: string[];
  notes?: string;
  revisions?: number;
}

export interface AgentTraceStep {
  agent: string;
  model: string;
  tokens_in: number;
  tokens_out: number;
  passed?: boolean | null;
  revision?: number;
}

export interface RunSection {
  section_code: string;
  name: string;
  order: number;
  status: RunSectionStatus;
  attempts: number;
  error: string | null;
  tokens_in: number;
  tokens_out: number;
  untraceable: string[];
  facts_count?: number;
  checks?: Record<string, AgentCheck>;
  agent_trace?: AgentTraceStep[];
}

export interface RunGap {
  doctype_code: string;
  reason: string;
}

export interface AppliedPreferences extends PreferenceProfileInput {
  source: 'user' | 'override' | 'org_default';
}

export interface RunMasterVersions {
  template: number;
  prompts: Record<string, number>;
  kpi_set: number | null;
  doctypes: Record<string, number>;
  global_rules: number | null;
}

export interface Run {
  id: string;
  case_id: string;
  template_key: string;
  status: RunStatus;
  cam_id: string | null;
  created_by: string;
  created_at: string;
  correlation_id: string;
  applied_preferences: AppliedPreferences;
  master_versions: RunMasterVersions;
  model_identity: string;
  gaps: RunGap[];
  sections: RunSection[];
}

// Shape of GET /api/runs?case_id= entries is not pinned down by the contract;
// treated as a conservative subset of Run (assumption noted in README).
export interface RunSummary {
  id: string;
  case_id?: string;
  template_key?: string;
  status: RunStatus;
  cam_id?: string | null;
  created_by?: string;
  created_at?: string;
}

// ---- CAMs (output service) -------------------------------------------------

export type CamStatus = 'draft' | 'final';

export interface CamSection {
  id: string;
  section_code: string;
  name: string;
  order: number;
  fixed_format: boolean;
  current_version_no: number;
  content: string;
  updated_at: string;
}

export interface Cam {
  id: string;
  case_id: string;
  run_id: string;
  title: string;
  template_key: string;
  status: CamStatus;
  finalised_by?: string | null;
  finalised_at?: string | null;
  created_at: string;
  sections: CamSection[];
}

// Shape of GET /api/cams?case_id= entries assumed to be a subset of Cam.
export interface CamSummary {
  id: string;
  case_id?: string;
  run_id?: string;
  title?: string;
  template_key?: string;
  status: CamStatus;
  created_at?: string;
}

export type SectionVersionSource = 'generated' | 'manual' | 'chat_suggestion' | 'regeneration';

export interface SectionVersion {
  section_id: string;
  version_no: number;
  name: string | null;
  source: SectionVersionSource;
  created_by: string;
  created_at: string;
}

export type SuggestionStatus = 'pending' | 'accepted' | 'rejected';

export interface Suggestion {
  id: string;
  cam_id: string;
  section_id: string;
  status: SuggestionStatus;
  instruction: string;
  proposed_content: string;
  diff: string;
  created_at: string;
  decided_by?: string | null;
  decided_at?: string | null;
}

export type ChatScope = 'document' | 'section';

export interface ChatMessage {
  id: string;
  cam_id: string;
  scope: ChatScope;
  section_id: string | null;
  role: 'user' | 'assistant';
  content: string;
  attached_document_ids: string[];
  created_at: string;
}

export interface ChatResponse {
  message: ChatMessage;
  reply: ChatMessage;
  suggestion: Suggestion | null;
}

export interface SuggestionDecision {
  suggestion: Suggestion;
  new_version?: SectionVersion;
}

// ---- Audit -----------------------------------------------------------------

export interface AuditEvent {
  id: string;
  seq: number;
  ts: string;
  actor: string;
  actor_roles: string[];
  action: string;
  entity_type: string;
  entity_id: string;
  case_id?: string | null;
  run_id?: string | null;
  cam_id?: string | null;
  correlation_id: string;
  detail: Record<string, unknown> | null;
  prev_hash: string;
  hash: string;
}

export interface AuditEventsPage {
  events: AuditEvent[];
  total: number;
}

export interface ChainVerification {
  intact: boolean;
  checked: number;
  first_break_seq: number | null;
}

// GET /api/audit/lineage/cam/{id} — exact shape not pinned by the contract;
// rendered defensively (assumption noted in README).
export interface CamLineage {
  run?: Record<string, unknown> | null;
  run_record?: Record<string, unknown> | null;
  events?: AuditEvent[];
  [key: string]: unknown;
}

export const AUDIT_ACTIONS: string[] = [
  'master.created', 'master.version_created', 'master.submitted', 'master.approved',
  'master.rejected', 'master.rolled_back', 'settings.updated', 'case.created',
  'document.uploaded', 'document.pulled', 'document.quarantined', 'document.deleted',
  'tag.auto_applied', 'tag.added', 'tag.changed', 'tag.removed', 'run.started',
  'run.section_completed', 'run.section_failed', 'run.section_retried',
  'run.section_regenerated', 'run.completed', 'cam.created', 'cam.section_edited',
  'cam.chat_message', 'cam.suggestion_created', 'cam.suggestion_accepted',
  'cam.suggestion_rejected', 'cam.finalised', 'cam.exported', 'user.login',
  'user.created', 'user.updated', 'prefs.updated',
];
