'use client';

import { useState } from 'react';
import { archiveSpam, resolveFlag } from '@/lib/api';

export function ArchiveSpamButton({ appId }: { appId: number }) {
  const [done, setDone] = useState(false);
  const [busy, setBusy] = useState(false);
  async function go() {
    setBusy(true);
    try {
      await archiveSpam(appId);
      setDone(true);
    } catch (e) {
      alert(`Archive failed: ${e}`);
    } finally {
      setBusy(false);
    }
  }
  if (done) return <span className="text-xs text-muted">archived</span>;
  return (
    <button className="text-xs text-muted hover:text-err" onClick={go} disabled={busy}>
      {busy ? '…' : 'Archive'}
    </button>
  );
}

export function ResolveFlagInline({
  flagId,
  options,
  defaultBankKey,
}: {
  flagId: number;
  options: string[];
  defaultBankKey: string;
}) {
  const [open, setOpen] = useState(false);
  const [value, setValue] = useState('');
  const [bankKey, setBankKey] = useState(defaultBankKey);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);

  async function go() {
    if (!value) {
      alert('Pick or type a value first');
      return;
    }
    setBusy(true);
    try {
      const r = await resolveFlag(flagId, value, bankKey);
      setDone(true);
      alert(
        `Wrote ${r.bank_key_written} → answer_bank.yml.${
          r.retry_run_id ? ` Retry queued: ${r.retry_run_id}` : ''
        }`,
      );
    } catch (e) {
      alert(`Resolve failed: ${e}`);
    } finally {
      setBusy(false);
    }
  }

  if (done) return <span className="text-xs text-ok">resolved</span>;
  if (!open) {
    return (
      <button
        className="text-xs text-accent hover:underline"
        onClick={() => setOpen(true)}
      >
        Provide answer
      </button>
    );
  }

  return (
    <div className="space-y-1">
      {options.length ? (
        <select
          className="input w-full text-xs"
          value={value}
          onChange={(e) => setValue(e.target.value)}
        >
          <option value="">— pick —</option>
          {options.map((o) => (
            <option key={o} value={o}>
              {o}
            </option>
          ))}
        </select>
      ) : (
        <input
          className="input w-full text-xs"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder="value"
        />
      )}
      <input
        className="input w-full text-xs"
        value={bankKey}
        onChange={(e) => setBankKey(e.target.value)}
        placeholder="bank key"
      />
      <div className="flex gap-1">
        <button className="btn-primary text-xs" onClick={go} disabled={busy}>
          {busy ? '…' : 'Save & retry'}
        </button>
        <button className="btn text-xs" onClick={() => setOpen(false)}>
          ×
        </button>
      </div>
    </div>
  );
}
