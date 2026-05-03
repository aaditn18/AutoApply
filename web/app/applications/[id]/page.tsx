import { notFound } from 'next/navigation';
import { getApplication } from '@/lib/api';
import {
  Crumb,
  OutcomePill,
  PageHeader,
  Pill,
  fmtDate,
  truncate,
} from '@/components/ui';
import { RetryButton } from '@/components/RetryButton';
import { PrefillButton } from '@/components/PrefillButton';

export const dynamic = 'force-dynamic';

const SOURCE_KIND: Record<string, 'ok' | 'warn' | 'err' | 'info'> = {
  profile: 'ok',
  bank: 'info',
  classifier: 'info',
  llm: 'warn',
  unknown: 'warn',
  needs_review: 'err',
};

function valueToString(v: unknown): string {
  if (v == null) return '—';
  if (typeof v === 'string') return v;
  if (typeof v === 'number' || typeof v === 'boolean') return String(v);
  try {
    return JSON.stringify(v);
  } catch {
    return String(v);
  }
}

export default async function ApplicationDetailPage({
  params,
}: {
  params: { id: string };
}) {
  const id = Number(params.id);
  if (!Number.isFinite(id)) notFound();

  let app;
  try {
    app = await getApplication(id);
  } catch (e) {
    if (String(e).includes('404')) notFound();
    throw e;
  }

  return (
    <div>
      <Crumb href="/applications" label="Back to Applications" />
      <PageHeader
        title={`${app.job_company} — ${app.job_title}`}
        subtitle={`app #${app.id} · submitted ${fmtDate(app.submitted_at)}`}
        right={
          <div className="flex items-center gap-2">
            {app.outcome === 'failed' ||
            app.outcome === 'captcha' ||
            app.outcome === 'review' ? (
              <>
                <PrefillButton appId={app.id} />
                <RetryButton appId={app.id} />
              </>
            ) : null}
            <a
              className="btn"
              href={app.job_url}
              target="_blank"
              rel="noreferrer"
            >
              Job posting ↗
            </a>
          </div>
        }
      />

      <div className="mb-6 flex flex-wrap gap-2">
        <OutcomePill value={app.outcome} />
        <Pill>{app.dry_run ? 'dry-run' : 'real submit'}</Pill>
        <Pill>track={app.track_submitted}</Pill>
        <Pill kind="info">{app.llm_model ?? 'no LLM'}</Pill>
        <Pill kind="info">{app.resolved_field_count} fields</Pill>
        {app.error_code ? <Pill kind="err">{app.error_code}</Pill> : null}
      </div>

      <div className="grid gap-6 lg:grid-cols-3">
        {/* Resolved fields */}
        <section className="lg:col-span-2">
          <h2 className="mb-3 text-sm font-semibold uppercase tracking-wider text-muted">
            Resolved fields ({app.resolved_fields.length})
          </h2>
          {app.resolved_fields.length === 0 ? (
            <div className="card text-sm text-muted">No fields recorded.</div>
          ) : (
            <div className="card overflow-x-auto p-0">
              <table className="w-full text-sm">
                <thead className="border-b border-border bg-bg/50 text-left text-xs uppercase text-muted">
                  <tr>
                    <th className="px-4 py-2">Source</th>
                    <th className="px-4 py-2">Field</th>
                    <th className="px-4 py-2">Value</th>
                  </tr>
                </thead>
                <tbody>
                  {app.resolved_fields.map((f) => (
                    <tr
                      key={f.label}
                      className="border-b border-border last:border-0 align-top"
                    >
                      <td className="px-4 py-2">
                        <Pill kind={SOURCE_KIND[f.source] ?? 'info'}>
                          {f.source}
                        </Pill>
                      </td>
                      <td className="px-4 py-2 font-mono text-xs">{f.label}</td>
                      <td className="px-4 py-2 text-text/80">
                        {truncate(valueToString(f.value), 200)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {app.cover_letter_text ? (
            <>
              <h2 className="mt-6 mb-3 text-sm font-semibold uppercase tracking-wider text-muted">
                Cover letter
              </h2>
              <pre className="card max-h-[400px] overflow-auto whitespace-pre-wrap text-sm leading-relaxed">
                {app.cover_letter_text}
              </pre>
            </>
          ) : null}
        </section>

        {/* Sidebar — screenshot + flags + audit */}
        <aside className="space-y-4">
          {app.screenshot_url ? (
            <div className="card">
              <h3 className="mb-2 text-sm font-semibold text-muted">
                Pre-submit screenshot
              </h3>
              <a href={app.screenshot_url} target="_blank" rel="noreferrer">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                  src={app.screenshot_url}
                  alt="pre-submit"
                  className="rounded border border-border"
                />
              </a>
            </div>
          ) : null}

          {app.review_flags.length ? (
            <div className="card">
              <h3 className="mb-2 text-sm font-semibold text-muted">
                Review flags ({app.review_flags.length})
              </h3>
              <ul className="space-y-2 text-sm">
                {app.review_flags.map((f) => (
                  <li
                    key={f.id}
                    className="rounded border border-border bg-bg/50 p-2"
                  >
                    <div className="flex items-center justify-between">
                      <Pill kind="warn">{f.reason}</Pill>
                      <span className="text-xs font-mono text-muted">
                        {f.field_kind}
                      </span>
                    </div>
                    <div className="mt-1 text-text/90">
                      {truncate(f.field_label, 140)}
                    </div>
                    {f.options.length ? (
                      <div className="mt-1 text-xs text-muted">
                        {f.options.length} option(s)
                      </div>
                    ) : null}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          {app.error_message ? (
            <div className="card">
              <h3 className="mb-2 text-sm font-semibold text-muted">Error</h3>
              <pre className="overflow-auto whitespace-pre-wrap text-xs">
                {app.error_message}
              </pre>
            </div>
          ) : null}

          {Object.keys(app.artifacts).length ? (
            <div className="card">
              <h3 className="mb-2 text-sm font-semibold text-muted">Artifacts</h3>
              <pre className="max-h-[300px] overflow-auto whitespace-pre-wrap text-xs">
                {JSON.stringify(app.artifacts, null, 2)}
              </pre>
            </div>
          ) : null}
        </aside>
      </div>
    </div>
  );
}
