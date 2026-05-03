// Hand-rolled types that mirror api/schemas.py.
//
// These are kept in sync manually for the MVP. After Phase 1 ships
// we run `make typegen` (the `npm run typegen` script in
// package.json) which downloads the OpenAPI spec from the running
// backend and regenerates this file via `openapi-typescript`. At
// that point the hand-rolled file becomes the auto-generated one.

export type JobScoring = {
  base_fit: number | null;
  pay_signal: number | null;
  pay_midpoint: number | null;
  loc_signal: number | null;
  freshness_signal: number | null;
  final_rank: number | null;
};

export type JobOut = {
  id: number;
  canonical_key: string;
  source: string;
  board_token: string;
  url: string;
  title: string;
  company: string;
  location: string;
  department: string;
  employment_type: string;
  posted_at: string;
  track: string | null;
  status: string;
  us_eligible: boolean;
  injection_detected: boolean;
  scoring: JobScoring;
  application_count: number;
  created_at: string;
  updated_at: string;
};

export type ApplicationOut = {
  id: number;
  job_id: number;
  job_title: string;
  job_company: string;
  job_source: string;
  track_submitted: string;
  dry_run: boolean;
  outcome: string;
  error_code: string;
  error_message: string;
  resolved_field_count: number;
  llm_model: string | null;
  submitted_at: string;
};

export type JobDetailOut = JobOut & {
  description: string;
  sightings: Record<string, unknown>;
  meta: Record<string, unknown>;
  applications: ApplicationOut[];
};

export type ResolvedField = {
  label: string;
  kind: string;
  value: unknown;
  source: string;
  confidence: number | null;
};

export type ReviewFlagOut = {
  id: number;
  job_id: number;
  application_id: number | null;
  field_name: string;
  field_label: string;
  field_kind: string;
  required: boolean;
  options: string[];
  reason: string;
  question_type: string | null;
  attempted_value: string;
  created_at: string;
};

export type ApplicationDetailOut = ApplicationOut & {
  answers: Record<string, unknown>;
  cover_letter_text: string;
  artifacts: Record<string, unknown>;
  resolved_fields: ResolvedField[];
  review_flags: ReviewFlagOut[];
  screenshot_url: string | null;
  job_url: string;
  job_description: string;
};

export type SpamRejectOut = {
  application_id: number;
  job_id: number;
  job_title: string;
  job_company: string;
  job_url: string;
  submitted_at: string;
  error_code: string;
  error_message: string;
};

export type OutcomeCounts = {
  ok: number;
  failed: number;
  review: number;
  captcha: number;
  dry_run: number;
  pending: number;
};

export type SourceSpamRate = {
  source: string;
  total_failed: number;
  spam_flagged: number;
  spam_rate: number;
};

export type TopJobOut = {
  id: number;
  title: string;
  company: string;
  track: string | null;
  final_rank: number | null;
  url: string;
};

export type DashboardOut = {
  total_jobs: number;
  scored_jobs: number;
  unapplied_scored: number;
  apps_total: number;
  apps_last_7d: number;
  outcomes: OutcomeCounts;
  spam_rates: SourceSpamRate[];
  top_unapplied: TopJobOut[];
  velocity: VelocityPoint[];
  last_updated: string;
};

export type Page<T> = {
  items: T[];
  total: number;
  page: number;
  page_size: number;
};

// ── Phase 2 — settings ─────────────────────────────────────────────

export type TrackProfileSummary = {
  track: string;
  full_name: string;
  email: string;
  phone: string;
  linkedin_url: string;
  github_url: string;
  skills_count: number;
  experiences_count: number;
  projects_count: number;
  education_count: number;
};

export type ProfileSnapshot = {
  tracks: TrackProfileSummary[];
  raw_path: string;
};

export type AnswerBankPayload = {
  yaml_text: string;
  parsed: Record<string, unknown>;
  keys: string[];
};

export type AnswerBankPreviewOut = {
  question_type: string;
  track: string | null;
  value: string | null;
  found: boolean;
};

export type CompaniesPayload = {
  sources: Record<string, Record<string, string[]>>;
};

export type EnvKey = {
  key: string;
  value: string;
  is_secret: boolean;
  is_set: boolean;
};

export type EnvPayload = {
  keys: EnvKey[];
  raw_path: string;
};

export type WriteResult = {
  ok: boolean;
  message: string;
  backup_path: string | null;
};

// ── Phase 3 — pipeline ─────────────────────────────────────────────

export type RunMeta = {
  run_id: string;
  stage: string;
  status: 'running' | 'ok' | 'failed';
  started_at: string;
  finished_at: string | null;
  duration_ms: number | null;
  summary: string;
  cmd: string;
  log_size: number;
};

export type RetryOut = {
  ok: boolean;
  new_application_id: number | null;
  outcome: string;
  message: string;
};

export type FlagResolveOut = {
  ok: boolean;
  bank_key_written: string;
  retry_run_id: string | null;
};

// ── Phase 4 — resumes + LLM audit + velocity ───────────────────────

export type ResumeOut = {
  track: string;
  pdf_path: string;
  pdf_size: number;
  tex_present: boolean;
  txt_present: boolean;
  last_modified: string | null;
};

export type LLMAuditRow = {
  application_id: number;
  submitted_at: string;
  job_company: string;
  job_title: string;
  model_used: string;
  batch_asked_count: number;
  cascade_trace: (string | Record<string, unknown>)[];
  error: string;
  answer_count: number;
};

export type VelocityPoint = {
  date: string;
  apps: number;
  ok: number;
  failed: number;
};
