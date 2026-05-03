/**
 * Typed fetch helpers for the FastAPI backend.
 *
 * Calls go to `/api/...` (relative URLs) so Next.js's rewrite rule
 * in next.config.mjs proxies them to http://127.0.0.1:8000. No env
 * vars to set, no CORS preflight in dev.
 *
 * Server components call these directly. Client components import
 * the same helpers — Next.js correctly threads them.
 */

import type {
  AnswerBankPayload,
  AnswerBankPreviewOut,
  ApplicationDetailOut,
  ApplicationOut,
  CompaniesPayload,
  DashboardOut,
  EnvPayload,
  FlagResolveOut,
  JobDetailOut,
  JobOut,
  LLMAuditRow,
  Page,
  ProfileSnapshot,
  ResumeOut,
  RetryOut,
  ReviewFlagOut,
  RunMeta,
  SpamRejectOut,
  WriteResult,
} from './types';

// Server components run on the server side and can't use relative URLs.
// Use 127.0.0.1 (the loopback the FastAPI binds to) when running on the
// server, falling back to relative URLs in the browser.
const BASE = typeof window === 'undefined'
  ? (process.env.AUTOAPPLY_API_BASE || 'http://127.0.0.1:8765')
  : '';

async function get<T>(path: string, params?: Record<string, unknown>): Promise<T> {
  const qs = params
    ? '?' + new URLSearchParams(
        Object.entries(params)
          .filter(([, v]) => v !== undefined && v !== null && v !== '')
          .flatMap(([k, v]) =>
            Array.isArray(v) ? v.map((x) => [k, String(x)]) : [[k, String(v)]],
          ) as [string, string][],
      ).toString()
    : '';
  const url = `${BASE}${path}${qs}`;
  const res = await fetch(url, { cache: 'no-store' });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`GET ${url} → ${res.status}: ${text}`);
  }
  return (await res.json()) as T;
}

// ── Dashboard ──────────────────────────────────────────────────────

export const getDashboard = () => get<DashboardOut>('/api/dashboard');

// ── Jobs ───────────────────────────────────────────────────────────

export type JobsQuery = {
  status?: string[];
  track?: string[];
  source?: string[];
  min_rank?: number;
  max_rank?: number;
  company?: string;
  posted_within_days?: number;
  us_eligible?: boolean;
  injection_detected?: boolean;
  has_applied?: boolean;
  page?: number;
  page_size?: number;
  order_by?: string;
};

export const listJobs = (q?: JobsQuery) =>
  get<Page<JobOut>>('/api/jobs', q as Record<string, unknown>);

export const getJob = (id: number) => get<JobDetailOut>(`/api/jobs/${id}`);

// ── Applications ───────────────────────────────────────────────────

export type AppsQuery = {
  outcome?: string[];
  dry_run?: boolean;
  track?: string[];
  source?: string[];
  error_code?: string;
  submitted_within_days?: number;
  page?: number;
  page_size?: number;
};

export const listApplications = (q?: AppsQuery) =>
  get<Page<ApplicationOut>>('/api/applications', q as Record<string, unknown>);

export const getApplication = (id: number) =>
  get<ApplicationDetailOut>(`/api/applications/${id}`);

// ── Review ─────────────────────────────────────────────────────────

export const listSpamRejects = (limit = 100) =>
  get<SpamRejectOut[]>('/api/review/spam', { limit });

export const listReviewFlags = (q?: {
  reason?: string[];
  page?: number;
  page_size?: number;
}) => get<Page<ReviewFlagOut>>('/api/review/flags', q as Record<string, unknown>);

// ── Phase 2: settings ──────────────────────────────────────────────

async function send<T>(method: 'PUT' | 'POST', path: string, body?: unknown): Promise<T> {
  const url = `${BASE}${path}`;
  const res = await fetch(url, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: body !== undefined ? JSON.stringify(body) : undefined,
    cache: 'no-store',
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${method} ${url} → ${res.status}: ${text}`);
  }
  return (await res.json()) as T;
}

export const getProfile = () => get<ProfileSnapshot>('/api/profile');

export const getAnswerBank = () => get<AnswerBankPayload>('/api/answer_bank');
export const putAnswerBank = (yamlText: string) =>
  send<WriteResult>('PUT', '/api/answer_bank', { yaml_text: yamlText });
export const previewAnswerBank = (questionType: string, track?: string) =>
  send<AnswerBankPreviewOut>('POST', '/api/answer_bank/preview', {
    question_type: questionType,
    track: track ?? null,
  });

export const getCompanies = () => get<CompaniesPayload>('/api/companies');
export const putCompanies = (sources: Record<string, Record<string, string[]>>) =>
  send<WriteResult>('PUT', '/api/companies', { sources });

export const getEnv = () => get<EnvPayload>('/api/env');
export const putEnv = (updates: Record<string, string>) =>
  send<WriteResult>('PUT', '/api/env', { updates });

// ── Phase 3: pipeline + retry ──────────────────────────────────────

export type TriggerBody = {
  sources?: string[];
  boards?: string[];
  limit?: number;
  min_rank?: number;
  dry_run?: boolean;
};

export const triggerStage = (
  stage: 'ingest' | 'score' | 'apply' | 'profile-build',
  body: TriggerBody = {},
) => send<{ run_id: string; stage: string }>('POST', `/api/pipeline/${stage}`, body);

export const listRuns = (limit = 50) =>
  get<RunMeta[]>('/api/pipeline/runs', { limit });

export const getRun = (runId: string) =>
  get<RunMeta>(`/api/pipeline/runs/${runId}`);

export const retryApplication = (id: number) =>
  send<RetryOut>('POST', `/api/applications/${id}/retry`);

export const batchApply = (jobIds: number[], dryRun = true) =>
  send<{ started: number; run_id: string }>('POST', '/api/applications/batch', {
    job_ids: jobIds,
    dry_run: dryRun,
  });

export const resolveFlag = (flagId: number, value: string, bankKey?: string) =>
  send<FlagResolveOut>('POST', `/api/review/flags/${flagId}/resolve`, {
    value,
    bank_key: bankKey ?? null,
  });

export const archiveSpam = (appId: number) =>
  send<WriteResult>('POST', `/api/review/spam/${appId}/archive`);

// ── Phase 4: resumes + llm audit ──────────────────────────────────

export const listResumes = () => get<ResumeOut[]>('/api/resumes');

export const listLLMAudit = (limit = 100) =>
  get<LLMAuditRow[]>('/api/llm_audit', { limit });
