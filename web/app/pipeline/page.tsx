'use client';

import { useEffect, useRef, useState } from 'react';
import { listRuns, triggerStage, type TriggerBody } from '@/lib/api';
import type { RunMeta } from '@/lib/types';
import { OutcomePill, PageHeader, Pill, fmtDate } from '@/components/ui';

type Stage = 'ingest' | 'score' | 'apply' | 'profile-build';

export default function PipelinePage() {
  const [runs, setRuns] = useState<RunMeta[]>([]);
  const [activeRun, setActiveRun] = useState<string | null>(null);
  const [logLines, setLogLines] = useState<string[]>([]);
  const [streaming, setStreaming] = useState(false);
  const logRef = useRef<HTMLPreElement | null>(null);

  async function refreshRuns() {
    try {
      setRuns(await listRuns(20));
    } catch (e) {
      console.error(e);
    }
  }

  useEffect(() => {
    refreshRuns();
    const t = setInterval(refreshRuns, 3000);
    return () => clearInterval(t);
  }, []);

  // Stream logs whenever activeRun changes.
  useEffect(() => {
    if (!activeRun) return;
    setLogLines([]);
    setStreaming(true);
    const url = `/api/pipeline/runs/${activeRun}/logs`;
    const es = new EventSource(url);
    es.addEventListener('message', (ev: MessageEvent) => {
      setLogLines((prev) => [...prev, ev.data]);
    });
    es.addEventListener('end', () => {
      setStreaming(false);
      es.close();
      refreshRuns();
    });
    es.onerror = () => {
      setStreaming(false);
      es.close();
    };
    return () => {
      es.close();
      setStreaming(false);
    };
  }, [activeRun]);

  // Auto-scroll log to bottom
  useEffect(() => {
    if (logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight;
    }
  }, [logLines]);

  async function trigger(stage: Stage, body: TriggerBody) {
    try {
      const r = await triggerStage(stage, body);
      setActiveRun(r.run_id);
    } catch (e) {
      alert(`Trigger failed: ${e}`);
    }
  }

  return (
    <div>
      <PageHeader
        title="Pipeline"
        subtitle="Trigger Ingest / Score / Apply, tail logs, see history"
      />

      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
        <IngestForm onSubmit={(b) => trigger('ingest', b)} />
        <ScoreForm onSubmit={(b) => trigger('score', b)} />
        <ApplyForm onSubmit={(b) => trigger('apply', b)} />
        <ProfileBuildForm onSubmit={() => trigger('profile-build', {})} />
      </div>

      {activeRun ? (
        <section className="mt-8">
          <div className="mb-2 flex items-center justify-between">
            <h2 className="text-sm font-semibold uppercase tracking-wider text-muted">
              Live log — {activeRun}
            </h2>
            {streaming ? (
              <Pill kind="info">streaming</Pill>
            ) : (
              <Pill kind="ok">finished</Pill>
            )}
          </div>
          <pre
            ref={logRef}
            className="h-[420px] overflow-auto rounded border border-border bg-bg p-3 font-mono text-xs leading-relaxed"
          >
            {logLines.join('\n') || '(waiting…)'}
          </pre>
        </section>
      ) : null}

      <section className="mt-8">
        <h2 className="mb-3 text-sm font-semibold uppercase tracking-wider text-muted">
          Recent runs
        </h2>
        {runs.length === 0 ? (
          <p className="text-sm text-muted">No runs yet.</p>
        ) : (
          <div className="card overflow-x-auto p-0">
            <table className="w-full text-sm">
              <thead className="border-b border-border bg-bg/50 text-left text-xs uppercase text-muted">
                <tr>
                  <th className="px-4 py-2">Stage</th>
                  <th className="px-4 py-2">Status</th>
                  <th className="px-4 py-2">Started</th>
                  <th className="px-4 py-2 text-right">Duration</th>
                  <th className="px-4 py-2">Summary</th>
                  <th className="px-4 py-2"></th>
                </tr>
              </thead>
              <tbody>
                {runs.map((r) => (
                  <tr
                    key={r.run_id}
                    className="border-b border-border last:border-0"
                  >
                    <td className="px-4 py-2 font-mono text-xs">{r.stage}</td>
                    <td className="px-4 py-2">
                      <OutcomePill value={r.status} />
                    </td>
                    <td className="px-4 py-2 text-xs text-muted whitespace-nowrap">
                      {fmtDate(r.started_at)}
                    </td>
                    <td className="px-4 py-2 text-right text-xs font-mono">
                      {r.duration_ms != null
                        ? `${(r.duration_ms / 1000).toFixed(1)}s`
                        : '—'}
                    </td>
                    <td className="px-4 py-2 text-xs">{r.summary}</td>
                    <td className="px-4 py-2 text-right">
                      <button
                        className="text-xs text-accent hover:underline"
                        onClick={() => setActiveRun(r.run_id)}
                      >
                        Tail logs
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}

// ── Per-stage forms ─────────────────────────────────────────────────

function IngestForm({ onSubmit }: { onSubmit: (b: TriggerBody) => void }) {
  const [src, setSrc] = useState<string>('');
  const [boards, setBoards] = useState<string>('');
  const [limit, setLimit] = useState<number>(50);
  return (
    <div className="card">
      <h3 className="mb-2 text-sm font-semibold">Ingest</h3>
      <div className="space-y-2">
        <select
          className="input w-full"
          value={src}
          onChange={(e) => setSrc(e.target.value)}
        >
          <option value="">all sources</option>
          <option value="greenhouse">greenhouse</option>
          <option value="lever">lever</option>
          <option value="ashby">ashby</option>
        </select>
        <input
          className="input w-full"
          placeholder="boards (comma-separated, optional)"
          value={boards}
          onChange={(e) => setBoards(e.target.value)}
        />
        <input
          className="input w-full"
          type="number"
          min={1}
          max={500}
          value={limit}
          onChange={(e) => setLimit(Number(e.target.value))}
        />
        <button
          className="btn-primary w-full"
          onClick={() =>
            onSubmit({
              sources: src ? [src] : undefined,
              boards: boards
                ? boards.split(',').map((b) => b.trim()).filter(Boolean)
                : undefined,
              limit,
            })
          }
        >
          Start ingest
        </button>
      </div>
    </div>
  );
}

function ScoreForm({ onSubmit }: { onSubmit: (b: TriggerBody) => void }) {
  const [limit, setLimit] = useState<number>(400);
  return (
    <div className="card">
      <h3 className="mb-2 text-sm font-semibold">Score</h3>
      <div className="space-y-2">
        <input
          className="input w-full"
          type="number"
          min={1}
          max={2000}
          value={limit}
          onChange={(e) => setLimit(Number(e.target.value))}
        />
        <button
          className="btn-primary w-full"
          onClick={() => onSubmit({ limit })}
        >
          Start score
        </button>
      </div>
    </div>
  );
}

function ApplyForm({ onSubmit }: { onSubmit: (b: TriggerBody) => void }) {
  const [limit, setLimit] = useState<number>(5);
  const [minRank, setMinRank] = useState<number>(0.6);
  const [dryRun, setDryRun] = useState<boolean>(true);
  return (
    <div className="card">
      <h3 className="mb-2 text-sm font-semibold">Apply</h3>
      <div className="space-y-2">
        <input
          className="input w-full"
          type="number"
          min={1}
          max={50}
          value={limit}
          onChange={(e) => setLimit(Number(e.target.value))}
          placeholder="limit"
        />
        <input
          className="input w-full"
          type="number"
          step="0.05"
          min={0}
          max={2}
          value={minRank}
          onChange={(e) => setMinRank(Number(e.target.value))}
          placeholder="min rank"
        />
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={dryRun}
            onChange={(e) => setDryRun(e.target.checked)}
          />
          dry-run
        </label>
        <button
          className={`w-full ${dryRun ? 'btn-primary' : 'btn-primary bg-err'}`}
          onClick={() => onSubmit({ limit, min_rank: minRank, dry_run: dryRun })}
        >
          {dryRun ? 'Start apply (dry)' : 'REAL apply'}
        </button>
      </div>
    </div>
  );
}

function ProfileBuildForm({ onSubmit }: { onSubmit: () => void }) {
  return (
    <div className="card">
      <h3 className="mb-2 text-sm font-semibold">Profile build</h3>
      <p className="mb-2 text-xs text-muted">
        Re-runs <code>autoapply profile-build</code>.
      </p>
      <button className="btn-primary w-full" onClick={onSubmit}>
        Rebuild
      </button>
    </div>
  );
}
