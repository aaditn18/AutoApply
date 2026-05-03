import Link from 'next/link';
import { listReviewFlags, listSpamRejects } from '@/lib/api';
import {
  Crumb,
  Empty,
  PageHeader,
  Pill,
  fmtDate,
  truncate,
} from '@/components/ui';
import { ArchiveSpamButton, ResolveFlagInline } from '@/components/ReviewActions';

export const dynamic = 'force-dynamic';

type SP = { [k: string]: string | string[] | undefined };
const asStr = (v: SP[string]) => (Array.isArray(v) ? v[0] : v);

function slugify(s: string): string {
  return (
    s
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '_')
      .replace(/^_+|_+$/g, '')
      .slice(0, 64) || 'unresolved'
  );
}

export default async function ReviewPage({
  searchParams,
}: {
  searchParams: SP;
}) {
  const tab = asStr(searchParams.tab) ?? 'spam';

  const [spam, flags] = await Promise.all([
    tab === 'spam' ? listSpamRejects(100) : Promise.resolve([]),
    tab === 'flags' ? listReviewFlags({ page_size: 50 }) : Promise.resolve(null),
  ]);

  return (
    <div>
      <Crumb href="/" label="Back to dashboard" />
      <PageHeader
        title="Review queue"
        subtitle="Things blocked for non-field-fill reasons"
      />

      <div className="mb-6 flex gap-2 text-sm">
        <Link
          href="?tab=spam"
          className={`btn ${tab === 'spam' ? 'border-accent text-accent' : ''}`}
        >
          Spam-flagged
        </Link>
        <Link
          href="?tab=flags"
          className={`btn ${tab === 'flags' ? 'border-accent text-accent' : ''}`}
        >
          Field-resolution flags
        </Link>
      </div>

      {tab === 'spam' ? (
        spam.length === 0 ? (
          <Empty message="No spam-flagged applications." />
        ) : (
          <div className="card overflow-x-auto p-0">
            <table className="w-full text-sm">
              <thead className="border-b border-border bg-bg/50 text-left text-xs uppercase text-muted">
                <tr>
                  <th className="px-4 py-2">Submitted</th>
                  <th className="px-4 py-2">Job</th>
                  <th className="px-4 py-2">Error code</th>
                  <th className="px-4 py-2"></th>
                </tr>
              </thead>
              <tbody>
                {spam.map((s) => (
                  <tr
                    key={s.application_id}
                    className="border-b border-border last:border-0"
                  >
                    <td className="px-4 py-2 text-xs text-muted whitespace-nowrap">
                      {fmtDate(s.submitted_at)}
                    </td>
                    <td className="px-4 py-2">
                      <Link
                        href={`/applications/${s.application_id}`}
                        className="font-medium hover:text-accent"
                      >
                        {s.job_company} — {truncate(s.job_title, 60)}
                      </Link>
                    </td>
                    <td className="px-4 py-2 font-mono text-xs">
                      {s.error_code}
                    </td>
                    <td className="px-4 py-2 text-right">
                      <div className="flex items-center justify-end gap-3">
                        <a
                          className="text-xs text-accent hover:underline"
                          href={s.job_url}
                          target="_blank"
                          rel="noreferrer"
                        >
                          Posting ↗
                        </a>
                        <ArchiveSpamButton appId={s.application_id} />
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )
      ) : null}

      {tab === 'flags' ? (
        flags && flags.items.length === 0 ? (
          <Empty message="No review flags." />
        ) : flags ? (
          <div className="card overflow-x-auto p-0">
            <table className="w-full text-sm">
              <thead className="border-b border-border bg-bg/50 text-left text-xs uppercase text-muted">
                <tr>
                  <th className="px-4 py-2">When</th>
                  <th className="px-4 py-2">Reason</th>
                  <th className="px-4 py-2">Field</th>
                  <th className="px-4 py-2">Kind</th>
                  <th className="px-4 py-2 text-right">App</th>
                </tr>
              </thead>
              <tbody>
                {flags.items.map((f) => (
                  <tr
                    key={f.id}
                    className="border-b border-border last:border-0 align-top"
                  >
                    <td className="px-4 py-2 text-xs text-muted whitespace-nowrap">
                      {fmtDate(f.created_at)}
                    </td>
                    <td className="px-4 py-2">
                      <Pill kind="warn">{f.reason}</Pill>
                    </td>
                    <td className="px-4 py-2">
                      {truncate(f.field_label, 100)}
                      {f.options.length ? (
                        <div className="text-xs text-muted">
                          {f.options.length} option(s):{' '}
                          {f.options.slice(0, 3).join(', ')}
                          {f.options.length > 3 ? '…' : ''}
                        </div>
                      ) : null}
                    </td>
                    <td className="px-4 py-2 font-mono text-xs">
                      {f.field_kind}
                    </td>
                    <td className="px-4 py-2 text-right space-y-1">
                      {f.application_id ? (
                        <Link
                          href={`/applications/${f.application_id}`}
                          className="block text-xs text-accent hover:underline"
                        >
                          #{f.application_id}
                        </Link>
                      ) : null}
                      <ResolveFlagInline
                        flagId={f.id}
                        options={f.options}
                        defaultBankKey={
                          f.question_type ?? slugify(f.field_label)
                        }
                      />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null
      ) : null}
    </div>
  );
}
