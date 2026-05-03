import { listResumes } from '@/lib/api';
import { PageHeader, Pill, fmtDate } from '@/components/ui';

export const dynamic = 'force-dynamic';

function fmtSize(bytes: number): string {
  if (!bytes) return '—';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
}

export default async function ResumesPage() {
  const resumes = await listResumes();
  return (
    <div>
      <PageHeader
        title="Resumes"
        subtitle="Per-track LaTeX → PDF compiled outputs"
      />

      <p className="mb-4 text-sm text-muted">
        Resumes live in the <code>resumes/</code> submodule. Edits happen
        there; this view is read-only. Use the Pipeline page&apos;s{' '}
        <code>profile-build</code> trigger after changes to refresh
        <code> state/profile.json</code>.
      </p>

      <div className="card overflow-x-auto p-0">
        <table className="w-full text-sm">
          <thead className="border-b border-border bg-bg/50 text-left text-xs uppercase text-muted">
            <tr>
              <th className="px-4 py-2">Track</th>
              <th className="px-4 py-2">PDF</th>
              <th className="px-4 py-2">.tex</th>
              <th className="px-4 py-2">.txt</th>
              <th className="px-4 py-2 text-right">Size</th>
              <th className="px-4 py-2">Last modified</th>
              <th className="px-4 py-2">Path</th>
            </tr>
          </thead>
          <tbody>
            {resumes.map((r) => (
              <tr key={r.track} className="border-b border-border last:border-0">
                <td className="px-4 py-2 font-mono text-sm uppercase">
                  {r.track}
                </td>
                <td className="px-4 py-2">
                  <Pill kind={r.pdf_size > 0 ? 'ok' : 'err'}>
                    {r.pdf_size > 0 ? 'present' : 'missing'}
                  </Pill>
                </td>
                <td className="px-4 py-2">
                  <Pill kind={r.tex_present ? 'ok' : 'warn'}>
                    {r.tex_present ? 'yes' : 'no'}
                  </Pill>
                </td>
                <td className="px-4 py-2">
                  <Pill kind={r.txt_present ? 'ok' : 'warn'}>
                    {r.txt_present ? 'yes' : 'no'}
                  </Pill>
                </td>
                <td className="px-4 py-2 text-right font-mono text-xs">
                  {fmtSize(r.pdf_size)}
                </td>
                <td className="px-4 py-2 text-xs text-muted">
                  {fmtDate(r.last_modified)}
                </td>
                <td className="px-4 py-2 font-mono text-xs text-muted">
                  {r.pdf_path.replace(
                    /^.*\/resumes\//,
                    'resumes/',
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
