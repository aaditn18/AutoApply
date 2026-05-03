import Link from 'next/link';
import { listJobs } from '@/lib/api';
import { Empty, PageHeader, Pill, fmtDate, truncate } from '@/components/ui';

export const dynamic = 'force-dynamic';

type SP = { [k: string]: string | string[] | undefined };

function asStr(v: string | string[] | undefined): string | undefined {
  if (Array.isArray(v)) return v[0];
  return v;
}

export default async function JobsPage({
  searchParams,
}: {
  searchParams: SP;
}) {
  const sourceParam = asStr(searchParams.source);
  const trackParam = asStr(searchParams.track);
  const page = Number(asStr(searchParams.page) ?? '1');

  const data = await listJobs({
    source: sourceParam ? [sourceParam] : undefined,
    track: trackParam ? [trackParam] : undefined,
    has_applied:
      asStr(searchParams.has_applied) === 'false'
        ? false
        : asStr(searchParams.has_applied) === 'true'
          ? true
          : undefined,
    min_rank: searchParams.min_rank
      ? Number(asStr(searchParams.min_rank))
      : undefined,
    page,
    page_size: 50,
  });

  return (
    <div>
      <PageHeader
        title="Jobs"
        subtitle={`${data.total.toLocaleString()} matches`}
      />

      <form className="mb-4 flex flex-wrap gap-2 text-sm">
        <select
          name="source"
          defaultValue={sourceParam ?? ''}
          className="input"
        >
          <option value="">all sources</option>
          <option value="greenhouse">greenhouse</option>
          <option value="lever">lever</option>
          <option value="ashby">ashby</option>
        </select>
        <select name="track" defaultValue={trackParam ?? ''} className="input">
          <option value="">all tracks</option>
          <option value="swe">swe</option>
          <option value="ml">ml</option>
          <option value="hpc">hpc</option>
          <option value="quant">quant</option>
        </select>
        <select
          name="has_applied"
          defaultValue={asStr(searchParams.has_applied) ?? ''}
          className="input"
        >
          <option value="">applied?</option>
          <option value="false">not applied</option>
          <option value="true">applied</option>
        </select>
        <input
          name="min_rank"
          type="number"
          step="0.05"
          placeholder="min rank"
          className="input w-28"
          defaultValue={asStr(searchParams.min_rank) ?? ''}
        />
        <button className="btn-primary" type="submit">
          Filter
        </button>
      </form>

      {data.items.length === 0 ? (
        <Empty message="No jobs match these filters." />
      ) : (
        <div className="card overflow-x-auto p-0">
          <table className="w-full text-sm">
            <thead className="border-b border-border bg-bg/50">
              <tr className="text-left text-xs uppercase text-muted">
                <th className="px-4 py-2">Title</th>
                <th className="px-4 py-2">Company</th>
                <th className="px-4 py-2">Source</th>
                <th className="px-4 py-2">Track</th>
                <th className="px-4 py-2 text-right">Rank</th>
                <th className="px-4 py-2">Status</th>
                <th className="px-4 py-2 text-right">#apps</th>
                <th className="px-4 py-2">Posted</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((j) => (
                <tr
                  key={j.id}
                  className="border-b border-border last:border-0 hover:bg-bg/30"
                >
                  <td className="px-4 py-2">
                    <Link
                      href={`/jobs/${j.id}`}
                      className="font-medium hover:text-accent"
                    >
                      {truncate(j.title, 60)}
                    </Link>
                  </td>
                  <td className="px-4 py-2">{j.company}</td>
                  <td className="px-4 py-2 font-mono text-xs">{j.source}</td>
                  <td className="px-4 py-2 font-mono text-xs">{j.track ?? '—'}</td>
                  <td className="px-4 py-2 text-right font-mono">
                    {j.scoring.final_rank?.toFixed(2) ?? '—'}
                  </td>
                  <td className="px-4 py-2">
                    <Pill kind={j.status === 'scored' ? 'info' : 'warn'}>
                      {j.status}
                    </Pill>
                  </td>
                  <td className="px-4 py-2 text-right">{j.application_count}</td>
                  <td className="px-4 py-2 text-xs text-muted">
                    {j.posted_at || fmtDate(j.created_at)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="mt-4 flex items-center justify-between text-sm text-muted">
        <span>
          Page {data.page} · {data.items.length} shown of {data.total}
        </span>
        <div className="flex gap-2">
          {data.page > 1 ? (
            <Link
              className="btn"
              href={`?${new URLSearchParams({
                ...Object.fromEntries(
                  Object.entries(searchParams)
                    .filter(([, v]) => typeof v === 'string')
                    .map(([k, v]) => [k, v as string]),
                ),
                page: String(data.page - 1),
              }).toString()}`}
            >
              Prev
            </Link>
          ) : null}
          {data.items.length === data.page_size ? (
            <Link
              className="btn"
              href={`?${new URLSearchParams({
                ...Object.fromEntries(
                  Object.entries(searchParams)
                    .filter(([, v]) => typeof v === 'string')
                    .map(([k, v]) => [k, v as string]),
                ),
                page: String(data.page + 1),
              }).toString()}`}
            >
              Next
            </Link>
          ) : null}
        </div>
      </div>
    </div>
  );
}
