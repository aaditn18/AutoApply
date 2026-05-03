'use client';

import { useState } from 'react';
import { putEnv } from '@/lib/api';
import { Pill } from '@/components/ui';
import type { EnvPayload } from '@/lib/types';

export function EnvTab({ data }: { data: EnvPayload }) {
  // For secrets: empty input = leave alone; non-empty = replace.
  // For non-secrets: input value is canonical.
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  function set(k: string, v: string) {
    setDraft((d) => ({ ...d, [k]: v }));
  }

  async function save() {
    setSaving(true);
    setError(null);
    setSuccess(null);
    // Build the updates payload — only include keys the user changed.
    const updates: Record<string, string> = {};
    for (const k of data.keys) {
      const v = draft[k.key];
      if (v === undefined) continue;
      // For secrets: "" means "don't change"; we just skip.
      if (k.is_secret && v === '') continue;
      updates[k.key] = v;
    }
    if (Object.keys(updates).length === 0) {
      setError('Nothing to save.');
      setSaving(false);
      return;
    }
    try {
      const r = await putEnv(updates);
      setSuccess(r.message);
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
          Edit local <code>.env</code>. Secret values are masked — leave the
          input blank to keep the existing value.
        </p>
        <div className="flex gap-2">
          {error ? <Pill kind="err">{error}</Pill> : null}
          {success ? <Pill kind="ok">{success}</Pill> : null}
          <button className="btn-primary" onClick={save} disabled={saving}>
            {saving ? 'Saving…' : 'Save'}
          </button>
        </div>
      </div>

      <div className="card overflow-x-auto p-0">
        <table className="w-full text-sm">
          <thead className="border-b border-border bg-bg/50 text-left text-xs uppercase text-muted">
            <tr>
              <th className="px-4 py-2">Key</th>
              <th className="px-4 py-2">Current</th>
              <th className="px-4 py-2">New value</th>
              <th className="px-4 py-2"></th>
            </tr>
          </thead>
          <tbody>
            {data.keys.map((k) => {
              const isBool =
                k.key === 'DRY_RUN' || k.key === 'TEST_SAFE_ONLY';
              return (
                <tr
                  key={k.key}
                  className="border-b border-border last:border-0 align-top"
                >
                  <td className="px-4 py-2 font-mono text-xs">{k.key}</td>
                  <td className="px-4 py-2 font-mono text-xs text-muted">
                    {k.value || (k.is_set ? '(empty)' : '(unset)')}
                  </td>
                  <td className="px-4 py-2">
                    {isBool ? (
                      <select
                        className="input"
                        value={draft[k.key] ?? k.value}
                        onChange={(e) => set(k.key, e.target.value)}
                      >
                        <option value="true">true</option>
                        <option value="false">false</option>
                      </select>
                    ) : (
                      <input
                        className="input w-full font-mono text-xs"
                        type={k.is_secret ? 'password' : 'text'}
                        placeholder={k.is_secret ? '(unchanged)' : ''}
                        value={draft[k.key] ?? (k.is_secret ? '' : k.value)}
                        onChange={(e) => set(k.key, e.target.value)}
                      />
                    )}
                  </td>
                  <td className="px-4 py-2">
                    {k.is_secret ? <Pill kind="warn">secret</Pill> : null}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <p className="mt-4 text-xs text-muted">
        File: <code>{data.raw_path}</code>
      </p>
    </div>
  );
}
