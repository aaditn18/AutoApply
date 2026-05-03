import Link from 'next/link';
import { notFound } from 'next/navigation';
import { getJob } from '@/lib/api';
import {
  Crumb,
  OutcomePill,
  PageHeader,
  Pill,
  fmtDate,
} from '@/components/ui';

export const dynamic = 'force-dynamic';

export default async function JobDetailPage({
  params,
}: {
  params: { id: string };
}) {
  const id = Number(params.id);
  if (!Number.isFinite(id)) notFound();

  let job;
  try {
    job = await getJob(id);
  } catch (e) {
    if (String(e).includes('404')) notFound();
    throw e;
  }

  const scoring = [
    ['base_fit', job.scoring.base_fit],
    ['pay_signal', job.scoring.pay_signal],
    ['loc_signal', job.scoring.loc_signal],
    ['freshness_signal', job.scoring.freshness_signal],
    ['final_rank', job.scoring.final_rank],
  ] as const;

  return (
    <div>
      <Crumb href="/jobs" label="Back to Jobs" />
      <PageHeader
        title={job.title}
        subtitle={`${job.company} · ${job.location || '—'} · ${job.source}/${job.board_token}`}
        right={
          <a className="btn-primary" href={job.url} target="_blank" rel="noreferrer">
            Open posting ↗
          </a>
        }
      />

      <div className="mb-6 grid gap-4 md:grid-cols-3">
        <div className="card md:col-span-2">
          <h3 className="mb-2 text-sm font-semibold text-muted">Description</h3>
          <pre className="max-h-[400px] overflow-auto whitespace-pre-wrap text-sm leading-relaxed text-text/90">
            {job.description || '— no description scraped —'}
          </pre>
        </div>
        <div className="card">
          <h3 className="mb-2 text-sm font-semibold text-muted">Scoring</h3>
          <dl className="space-y-1 text-sm">
            {scoring.map(([label, value]) => (
              <div key={label} className="flex justify-between">
                <dt className="text-muted">{label}</dt>
                <dd className="font-mono">
                  {value == null ? '—' : value.toFixed(3)}
                </dd>
              </div>
            ))}
            <div className="mt-3 flex flex-wrap gap-2 text-xs">
              <Pill kind={job.us_eligible ? 'ok' : 'err'}>
                {job.us_eligible ? 'US-eligible' : 'not US'}
              </Pill>
              {job.injection_detected ? (
                <Pill kind="err">injection!</Pill>
              ) : null}
              {job.track ? <Pill>{job.track}</Pill> : null}
              <Pill kind={job.status === 'scored' ? 'info' : 'warn'}>
                {job.status}
              </Pill>
            </div>
          </dl>
        </div>
      </div>

      <h3 className="mb-3 text-sm font-semibold uppercase tracking-wider text-muted">
        Applications ({job.applications.length})
      </h3>
      {job.applications.length === 0 ? (
        <p className="text-sm text-muted">No applications yet.</p>
      ) : (
        <div className="card divide-y divide-border p-0">
          {job.applications.map((a) => (
            <Link
              key={a.id}
              href={`/applications/${a.id}`}
              className="flex items-center justify-between gap-4 px-4 py-3 hover:bg-bg/50"
            >
              <div>
                <div className="text-sm font-medium">
                  app #{a.id} — {fmtDate(a.submitted_at)}
                </div>
                <div className="text-xs text-muted">
                  {a.dry_run ? 'dry-run' : 'real'} · track={a.track_submitted} ·
                  {' '}
                  {a.resolved_field_count} fields · {a.llm_model ?? 'no LLM'}
                </div>
              </div>
              <OutcomePill value={a.outcome} />
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
