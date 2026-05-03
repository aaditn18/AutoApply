import Link from 'next/link';
import { listApplications } from '@/lib/api';
import {
  Empty,
  OutcomePill,
  PageHeader,
  fmtDate,
  truncate,
} from '@/components/ui';

export const dynamic = 'force-dynamic';

type SP = { [k: string]: string | string[] | undefined };
const asStr = (v: SP[string]) => (Array.isArray(v) ? v[0] : v);

export default async function ApplicationsPage({
  searchParams,
}: {
  searchParams: SP;
}) {
  const outcome = asStr(searchParams.outcome);
  const source = asStr(searchParams.source);
  const dryRun = asStr(searchParams.dry_run);
  const page = Number(asStr(searchParams.page) ?? '1');

  const data = await listApplications({
    outcome: outcome ? [outcome] : undefined,
    source: source ? [source] : undefined,
    dry_run: dryRun === 'true' ? true : dryRun === 'false' ? false : undefined,
    page,
    page_size: 50,
  });

  return (
    <div>
      <PageHeader
        title="Applications"
        subtitle={`${data.total.toLocaleString()} matches`}
      />

      <form className="mb-4 flex flex-wrap gap-2 text-sm">
        <select name="outcome" defaultValue={outcome ?? ''} className="input">
          <option value="">all outcomes</option>
          <option value="ok">ok</option>
          <option value="failed">failed</option>
          <option value="review">review</option>
          <option value="captcha">captcha</option>
          <option value="dry_run">dry_run</option>
        </select>
        <select name="source" defaultValue={source ?? ''} className="input">
          <option value="">all sources</option>
          <option value="greenhouse">greenhouse</option>
          <option value="lever">lever</option>
          <option value="ashby">ashby</option>
        </select>
        <select name="dry_run" defaultValue={dryRun ?? ''} className="input">
          <option value="">all</option>
          <option value="false">real</option>
          <option value="true">dry-run</option>
        </select>
        <button className="btn-primary" type="submit">
          Filter
        </button>
      </form>

      {data.items.length === 0 ? (
        <Empty message="No applications match these filters." />
      ) : (
        <div className="card overflow-x-auto p-0">
          <table className="w-full text-sm">
            <thead className="border-b border-border bg-bg/50">
              <tr className="text-left text-xs uppercase text-muted">
                <th className="px-4 py-2">Submitted</th>
                <th className="px-4 py-2">Outcome</th>
                <th className="px-4 py-2">Job</th>
                <th className="px-4 py-2">Source</th>
                <th className="px-4 py-2">Track</th>
                <th className="px-4 py-2 text-right">#fields</th>
                <th className="px-4 py-2">LLM</th>
                <th className="px-4 py-2">Error</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((a) => (
                <tr
                  key={a.id}
                  className="border-b border-border last:border-0 hover:bg-bg/30"
                >
                  <td className="px-4 py-2 whitespace-nowrap text-xs text-muted">
                    {fmtDate(a.submitted_at)}
                  </td>
                  <td className="px-4 py-2">
                    <OutcomePill value={a.outcome} />
                    {a.dry_run ? (
                      <span className="ml-2 text-xs text-muted">dry</span>
                    ) : null}
                  </td>
                  <td className="px-4 py-2">
                    <Link
                      href={`/applications/${a.id}`}
                      className="font-medium hover:text-accent"
                    >
                      {truncate(`${a.job_company} — ${a.job_title}`, 70)}
                    </Link>
                  </td>
                  <td className="px-4 py-2 font-mono text-xs">{a.job_source}</td>
                  <td className="px-4 py-2 font-mono text-xs">
                    {a.track_submitted}
                  </td>
                  <td className="px-4 py-2 text-right">
                    {a.resolved_field_count}
                  </td>
                  <td className="px-4 py-2 font-mono text-xs">
                    {a.llm_model ?? '—'}
                  </td>
                  <td className="px-4 py-2 text-xs text-muted">
                    {truncate(a.error_code, 40)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="mt-4 flex items-center justify-between text-sm text-muted">
        <span>
          Page {data.page} · {data.items.length} of {data.total}
        </span>
      </div>
    </div>
  );
}
