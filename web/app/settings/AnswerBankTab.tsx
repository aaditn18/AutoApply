'use client';

import { useState } from 'react';
import { previewAnswerBank, putAnswerBank } from '@/lib/api';
import { Pill } from '@/components/ui';
import type { AnswerBankPayload, AnswerBankPreviewOut } from '@/lib/types';

export function AnswerBankTab({ data }: { data: AnswerBankPayload }) {
  const [text, setText] = useState(data.yaml_text);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [previewKey, setPreviewKey] = useState('');
  const [previewTrack, setPreviewTrack] = useState('');
  const [previewResult, setPreviewResult] =
    useState<AnswerBankPreviewOut | null>(null);

  const dirty = text !== data.yaml_text;

  async function save() {
    setSaving(true);
    setError(null);
    setSuccess(null);
    try {
      const result = await putAnswerBank(text);
      setSuccess(result.backup_path
        ? `Saved (backup: ${result.backup_path.split('/').pop()})`
        : 'Saved');
      // refresh page after a beat so the backend re-reads
      setTimeout(() => window.location.reload(), 800);
    } catch (e) {
      setError(String(e).split(':').slice(2).join(':').trim() || String(e));
    } finally {
      setSaving(false);
    }
  }

  async function runPreview() {
    if (!previewKey) return;
    try {
      const r = await previewAnswerBank(previewKey, previewTrack || undefined);
      setPreviewResult(r);
    } catch (e) {
      setError(String(e));
    }
  }

  return (
    <div className="grid gap-6 lg:grid-cols-3">
      <div className="lg:col-span-2">
        <div className="mb-2 flex items-center justify-between">
          <h3 className="text-sm font-semibold uppercase tracking-wider text-muted">
            answer_bank.yml ({data.keys.length} keys)
          </h3>
          <div className="flex items-center gap-2">
            {error ? <Pill kind="err">{error}</Pill> : null}
            {success ? <Pill kind="ok">{success}</Pill> : null}
            <button
              className="btn-primary disabled:opacity-50"
              onClick={save}
              disabled={!dirty || saving}
            >
              {saving ? 'Saving…' : 'Save'}
            </button>
          </div>
        </div>
        <textarea
          className="h-[600px] w-full rounded border border-border bg-bg px-3 py-2
                     font-mono text-xs leading-relaxed focus:outline-none
                     focus:ring-1 focus:ring-accent"
          value={text}
          onChange={(e) => setText(e.target.value)}
          spellCheck={false}
        />
      </div>

      <aside className="space-y-4">
        <div className="card">
          <h3 className="mb-2 text-sm font-semibold text-muted">
            Preview a key
          </h3>
          <div className="space-y-2">
            <select
              className="input w-full"
              value={previewKey}
              onChange={(e) => setPreviewKey(e.target.value)}
            >
              <option value="">— pick a key —</option>
              {data.keys.map((k) => (
                <option key={k} value={k}>
                  {k}
                </option>
              ))}
            </select>
            <select
              className="input w-full"
              value={previewTrack}
              onChange={(e) => setPreviewTrack(e.target.value)}
            >
              <option value="">any track</option>
              <option value="swe">swe</option>
              <option value="ml">ml</option>
              <option value="hpc">hpc</option>
              <option value="quant">quant</option>
            </select>
            <button className="btn w-full" onClick={runPreview}>
              Resolve
            </button>
          </div>
          {previewResult ? (
            <div className="mt-3 rounded border border-border bg-bg/50 p-2 text-xs">
              <div>
                <Pill kind={previewResult.found ? 'ok' : 'warn'}>
                  {previewResult.found ? 'found' : 'miss'}
                </Pill>
              </div>
              <pre className="mt-2 whitespace-pre-wrap break-words">
                {previewResult.value ?? '(none)'}
              </pre>
            </div>
          ) : null}
        </div>
      </aside>
    </div>
  );
}
