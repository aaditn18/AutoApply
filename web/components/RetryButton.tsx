'use client';

import { useState } from 'react';
import { retryApplication } from '@/lib/api';

export function RetryButton({ appId }: { appId: number }) {
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  async function go() {
    setBusy(true);
    try {
      const r = await retryApplication(appId);
      setMsg(r.message);
    } catch (e) {
      setMsg(`failed: ${e}`);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="inline-flex items-center gap-2">
      <button className="btn" onClick={go} disabled={busy}>
        {busy ? 'Retrying…' : 'Retry'}
      </button>
      {msg ? <span className="text-xs text-muted">{msg}</span> : null}
    </div>
  );
}
