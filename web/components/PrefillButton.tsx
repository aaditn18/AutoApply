'use client';

/**
 * "Open prefilled" — calls POST /api/applications/{id}/prefill which
 * spawns a non-headless Chromium window on the API host (i.e. your
 * local machine when running `make dev`). Every field is auto-filled
 * from the failed Application's stored answers; the user reviews,
 * solves any captcha / OTP / spam-flag, and clicks Submit themselves.
 *
 * Unlike RetryButton (which re-runs the full apply pipeline including
 * the Submit click), this button never submits on the user's behalf.
 * It's the safe path for failed-state recovery.
 */

import { useState } from 'react';

type PrefillResponse = {
  ok: boolean;
  application_id: number;
  job_id: number;
  source: string;
  url: string;
  fields_count: number;
  resume_path: string;
};

export function PrefillButton({ appId }: { appId: number }) {
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  async function go() {
    setBusy(true);
    setMsg(null);
    try {
      const res = await fetch(`/api/applications/${appId}/prefill`, {
        method: 'POST',
      });
      const body = (await res.json()) as PrefillResponse | { detail: string };
      if (!res.ok) {
        const detail = (body as { detail: string }).detail || 'failed';
        throw new Error(detail);
      }
      const r = body as PrefillResponse;
      setMsg(
        `Opening… ${r.fields_count} fields prefilled, browser will appear shortly. Close the window when done.`,
      );
    } catch (e) {
      setMsg(`failed: ${e}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="inline-flex items-center gap-2">
      <button
        className="btn-primary disabled:opacity-50"
        onClick={go}
        disabled={busy}
        title="Opens a real browser window with the form already filled — you only have to click Submit"
      >
        {busy ? 'Opening…' : 'Open prefilled'}
      </button>
      {msg ? (
        <span className="max-w-[420px] text-xs text-muted">{msg}</span>
      ) : null}
    </div>
  );
}
