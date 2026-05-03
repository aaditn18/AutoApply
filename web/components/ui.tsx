/**
 * Tiny shared UI primitives. We deliberately avoid pulling in a
 * component library (shadcn/ui, Radix, etc.) for the MVP — every
 * piece below is one-shot Tailwind. Once we have ~3 instances of a
 * pattern we extract; until then keep the surface flat.
 */

import type { ReactNode } from 'react';
import Link from 'next/link';

export function Pill({
  kind = 'info',
  children,
}: {
  kind?: 'ok' | 'warn' | 'err' | 'info';
  children: ReactNode;
}) {
  const cls =
    kind === 'ok'
      ? 'pill pill-ok'
      : kind === 'warn'
        ? 'pill pill-warn'
        : kind === 'err'
          ? 'pill pill-err'
          : 'pill pill-info';
  return <span className={cls}>{children}</span>;
}

const OUTCOME_KIND: Record<string, 'ok' | 'warn' | 'err' | 'info'> = {
  ok: 'ok',
  failed: 'err',
  captcha: 'err',
  review: 'warn',
  dry_run: 'info',
  pending: 'info',
};

export function OutcomePill({ value }: { value: string }) {
  return <Pill kind={OUTCOME_KIND[value] ?? 'info'}>{value}</Pill>;
}

export function Stat({
  label,
  value,
  hint,
}: {
  label: string;
  value: string | number;
  hint?: string;
}) {
  return (
    <div className="card">
      <div className="text-xs uppercase tracking-wider text-muted">{label}</div>
      <div className="mt-1 text-2xl font-semibold">{value}</div>
      {hint ? <div className="mt-1 text-xs text-muted">{hint}</div> : null}
    </div>
  );
}

export function PageHeader({
  title,
  subtitle,
  right,
}: {
  title: string;
  subtitle?: string;
  right?: ReactNode;
}) {
  return (
    <div className="mb-6 flex items-center justify-between gap-4">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
        {subtitle ? (
          <p className="mt-1 text-sm text-muted">{subtitle}</p>
        ) : null}
      </div>
      {right ? <div>{right}</div> : null}
    </div>
  );
}

export function Empty({ message }: { message: string }) {
  return (
    <div className="card text-center text-sm text-muted">{message}</div>
  );
}

export function Crumb({ href, label }: { href: string; label: string }) {
  return (
    <Link
      href={href}
      className="text-xs text-muted hover:text-text transition-colors"
    >
      ← {label}
    </Link>
  );
}

export function fmtDate(s: string | null | undefined): string {
  if (!s) return '—';
  try {
    return new Date(s).toLocaleString();
  } catch {
    return s;
  }
}

export function fmtPct(x: number | null | undefined, digits = 0): string {
  if (x == null) return '—';
  return (x * 100).toFixed(digits) + '%';
}

export function truncate(s: string | null | undefined, n = 60): string {
  if (!s) return '';
  return s.length > n ? s.slice(0, n) + '…' : s;
}
