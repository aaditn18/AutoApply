import Link from 'next/link';
import { listLLMAudit } from '@/lib/api';
import { PageHeader, Pill, fmtDate, truncate } from '@/components/ui';

export const dynamic = 'force-dynamic';

export default async function LLMAuditPage() {
  const rows = await listLLMAudit(200);
  const byModel = rows.reduce<Record<string, number>>((acc, r) => {
    acc[r.model_used] = (acc[r.model_used] || 0) + 1;
    return acc;
  }, {});
  const totalAsked = rows.reduce((acc, r) => acc + r.batch_asked_count, 0);

  return (
    <div>
      <PageHeader
        title="LLM audit"
        subtitle={`${rows.length} application(s) used the batch LLM`}
      />

      <div className="mb-6 grid gap-4 md:grid-cols-3">
        <div className="card">
          <h3 className="text-xs uppercase text-muted">Total LLM calls</h3>
          <p className="mt-1 text-2xl font-semibold">{rows.length}</p>
        </div>
        <div className="card">
          <h3 className="text-xs uppercase text-muted">Total questions asked</h3>
          <p className="mt-1 text-2xl font-semibold">{totalAsked}</p>
        </div>
        <div className="card">
          <h3 className="mb-1 text-xs uppercase text-muted">By model</h3>
          <ul className="space-y-0.5 text-xs">
            {Object.entries(byModel)
              .sort((a, b) => b[1] - a[1])
              .map(([m, n]) => (
                <li key={m} className="flex justify-between">
                  <span className="font-mono">{m}</span>
                  <span>{n}</span>
                </li>
              ))}
          </ul>
        </div>
      </div>

      {rows.length === 0 ? (
        <p className="text-sm text-muted">No LLM activity yet.</p>
      ) : (
        <div className="card overflow-x-auto p-0">
          <table className="w-full text-sm">
            <thead className="border-b border-border bg-bg/50 text-left text-xs uppercase text-muted">
              <tr>
                <th className="px-4 py-2">When</th>
                <th className="px-4 py-2">Job</th>
                <th className="px-4 py-2">Model</th>
                <th className="px-4 py-2 text-right">#asked</th>
                <th className="px-4 py-2 text-right">#answers</th>
                <th className="px-4 py-2">Cascade</th>
                <th className="px-4 py-2">Error</th>
                <th className="px-4 py-2 text-right">App</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr
                  key={r.application_id}
                  className="border-b border-border last:border-0 align-top"
                >
                  <td className="px-4 py-2 text-xs text-muted whitespace-nowrap">
                    {fmtDate(r.submitted_at)}
                  </td>
                  <td className="px-4 py-2">
                    {truncate(`${r.job_company} — ${r.job_title}`, 60)}
                  </td>
                  <td className="px-4 py-2 font-mono text-xs">{r.model_used}</td>
                  <td className="px-4 py-2 text-right">
                    {r.batch_asked_count}
                  </td>
                  <td className="px-4 py-2 text-right">{r.answer_count}</td>
                  <td className="px-4 py-2 text-xs text-muted">
                    {r.cascade_trace.length
                      ? r.cascade_trace
                          .slice(0, 3)
                          .map((step) =>
                            typeof step === 'string'
                              ? step
                              : ((step as Record<string, unknown>).model as string) ?? '?',
                          )
                          .join(' → ')
                      : '—'}
                  </td>
                  <td className="px-4 py-2 text-xs">
                    {r.error ? <Pill kind="err">err</Pill> : '—'}
                  </td>
                  <td className="px-4 py-2 text-right">
                    <Link
                      href={`/applications/${r.application_id}`}
                      className="text-xs text-accent hover:underline"
                    >
                      #{r.application_id}
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
