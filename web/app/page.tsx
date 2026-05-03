import Link from 'next/link';
import { getDashboard } from '@/lib/api';
import { PageHeader, Stat, fmtPct, fmtDate } from '@/components/ui';
import { VelocitySparkline } from '@/components/Sparkline';

export const dynamic = 'force-dynamic';

export default async function DashboardPage() {
  const d = await getDashboard();
  return (
    <div>
      <PageHeader
        title="Dashboard"
        subtitle={`Last updated ${fmtDate(d.last_updated)}`}
      />

      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <Stat label="Jobs" value={d.total_jobs} hint={`${d.scored_jobs} scored`} />
        <Stat
          label="Unapplied scored"
          value={d.unapplied_scored}
          hint="ready to apply"
        />
        <Stat
          label="Applications"
          value={d.apps_total}
          hint={`${d.apps_last_7d} in last 7d`}
        />
        <Stat
          label="OK / Failed / Review"
          value={`${d.outcomes.ok} / ${d.outcomes.failed} / ${d.outcomes.review}`}
          hint={`+ ${d.outcomes.captcha} captcha, ${d.outcomes.dry_run} dry-run`}
        />
      </div>

      <div className="mt-8">
        <VelocitySparkline points={d.velocity} />
      </div>

      <h2 className="mt-10 mb-3 text-sm font-semibold uppercase tracking-wider text-muted">
        Spam-flag rate by ATS
      </h2>
      <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
        {d.spam_rates.map((r) => (
          <div key={r.source} className="card">
            <div className="flex items-center justify-between">
              <span className="font-mono text-sm uppercase">{r.source}</span>
              <span className="text-sm text-muted">
                {r.spam_flagged}/{r.total_failed}
              </span>
            </div>
            <div className="mt-2 h-2 overflow-hidden rounded bg-bg">
              <div
                className="h-full bg-err"
                style={{ width: `${Math.min(100, r.spam_rate * 100)}%` }}
              />
            </div>
            <div className="mt-2 text-xs text-muted">
              {fmtPct(r.spam_rate, 1)} of failed apps are spam-flagged
            </div>
          </div>
        ))}
      </div>

      <h2 className="mt-10 mb-3 text-sm font-semibold uppercase tracking-wider text-muted">
        Top unapplied scored jobs
      </h2>
      {d.top_unapplied.length === 0 ? (
        <p className="text-sm text-muted">No scored jobs without an application.</p>
      ) : (
        <div className="card divide-y divide-border p-0">
          {d.top_unapplied.map((j) => (
            <Link
              key={j.id}
              href={`/jobs/${j.id}`}
              className="flex items-center justify-between gap-4 px-4 py-3 hover:bg-bg/50"
            >
              <div className="min-w-0 flex-1">
                <div className="truncate font-medium">{j.title}</div>
                <div className="text-xs text-muted">
                  {j.company} · {j.track ?? '—'}
                </div>
              </div>
              <span className="font-mono text-sm">
                {j.final_rank?.toFixed(2) ?? '—'}
              </span>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
