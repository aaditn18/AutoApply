'use client';

import { useState } from 'react';
import { putCompanies } from '@/lib/api';
import { Pill } from '@/components/ui';
import type { CompaniesPayload } from '@/lib/types';

const SOURCES = ['greenhouse', 'lever', 'ashby'] as const;
const TIERS = ['test_safe', 'live_only'] as const;

export function CompaniesTab({ data }: { data: CompaniesPayload }) {
  const [grid, setGrid] = useState(() => {
    // Ensure every (source, tier) cell exists.
    const seed: Record<string, Record<string, string[]>> = {};
    for (const s of SOURCES) {
      seed[s] = {};
      for (const t of TIERS) {
        seed[s][t] = [...(data.sources[s]?.[t] ?? [])];
      }
    }
    return seed;
  });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  function update(source: string, tier: string, tokens: string[]) {
    setGrid((g) => ({
      ...g,
      [source]: { ...g[source], [tier]: tokens },
    }));
  }

  async function save() {
    setSaving(true);
    setError(null);
    setSuccess(null);
    try {
      await putCompanies(grid);
      setSuccess('Saved');
      setTimeout(() => window.location.reload(), 800);
    } catch (e) {
      const msg = String(e);
      setError(msg.split(':').slice(2).join(':').trim() || msg);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <p className="text-sm text-muted">
          Tokens are the board slugs from each ATS. <code>test_safe</code> is
          ingested when <code>TEST_SAFE_ONLY=true</code>; <code>live_only</code>{' '}
          unlocks when you flip it.
        </p>
        <div className="flex gap-2">
          {error ? <Pill kind="err">{error}</Pill> : null}
          {success ? <Pill kind="ok">{success}</Pill> : null}
          <button className="btn-primary" onClick={save} disabled={saving}>
            {saving ? 'Saving…' : 'Save'}
          </button>
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        {SOURCES.map((src) => (
          <div key={src} className="card">
            <h3 className="mb-3 font-mono text-sm uppercase tracking-wider text-accent">
              {src}
            </h3>
            {TIERS.map((tier) => (
              <CompanyTierEditor
                key={tier}
                source={src}
                tier={tier}
                tokens={grid[src][tier]}
                onChange={(t) => update(src, tier, t)}
              />
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}

function CompanyTierEditor({
  tier,
  tokens,
  onChange,
}: {
  source: string;
  tier: string;
  tokens: string[];
  onChange: (t: string[]) => void;
}) {
  const [draft, setDraft] = useState('');

  function add() {
    const v = draft.trim();
    if (!v) return;
    if (tokens.includes(v)) {
      setDraft('');
      return;
    }
    onChange([...tokens, v]);
    setDraft('');
  }

  function remove(token: string) {
    onChange(tokens.filter((t) => t !== token));
  }

  return (
    <div className="mb-4">
      <div className="mb-2 flex items-center justify-between">
        <span className="text-xs font-medium uppercase text-muted">
          {tier}
        </span>
        <span className="text-xs text-muted">{tokens.length} token(s)</span>
      </div>
      <div className="mb-2 flex flex-wrap gap-1">
        {tokens.map((t) => (
          <span
            key={t}
            className="group flex items-center gap-1 rounded bg-bg px-2 py-0.5 text-xs font-mono"
          >
            {t}
            <button
              className="text-muted hover:text-err"
              onClick={() => remove(t)}
              aria-label={`remove ${t}`}
            >
              ×
            </button>
          </span>
        ))}
      </div>
      <div className="flex gap-1">
        <input
          className="input flex-1"
          placeholder="add token…"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault();
              add();
            }
          }}
        />
        <button className="btn" onClick={add}>
          Add
        </button>
      </div>
    </div>
  );
}
