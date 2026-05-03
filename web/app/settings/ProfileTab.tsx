import type { ProfileSnapshot } from '@/lib/types';

export function ProfileTab({ data }: { data: ProfileSnapshot }) {
  return (
    <div>
      <p className="mb-4 text-sm text-muted">
        Profile is read-only here — it&apos;s rebuilt from{' '}
        <code>state/profile.json</code> by the resume submodule. Use the
        Pipeline page&apos;s &ldquo;Profile build&rdquo; trigger (Phase 3) or
        run <code>autoapply profile-build</code> from the CLI.
      </p>

      <div className="grid gap-4 md:grid-cols-2">
        {data.tracks.map((t) => (
          <div key={t.track} className="card">
            <div className="mb-2 flex items-center justify-between">
              <h3 className="font-mono text-sm uppercase tracking-wider">
                {t.track}
              </h3>
              <span className="text-xs text-muted">
                {t.skills_count} skills · {t.experiences_count} experiences ·{' '}
                {t.projects_count} projects
              </span>
            </div>
            <dl className="space-y-1 text-sm">
              <div className="flex justify-between">
                <dt className="text-muted">Name</dt>
                <dd className="font-medium">{t.full_name || '—'}</dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-muted">Email</dt>
                <dd className="font-mono text-xs">{t.email || '—'}</dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-muted">Phone</dt>
                <dd className="font-mono text-xs">{t.phone || '—'}</dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-muted">LinkedIn</dt>
                <dd className="truncate font-mono text-xs">
                  {t.linkedin_url || '—'}
                </dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-muted">GitHub</dt>
                <dd className="truncate font-mono text-xs">
                  {t.github_url || '—'}
                </dd>
              </div>
            </dl>
          </div>
        ))}
      </div>

      <p className="mt-6 text-xs text-muted">
        File: <code>{data.raw_path}</code>
      </p>
    </div>
  );
}
