import type { Metadata } from 'next';
import Link from 'next/link';
import './globals.css';

export const metadata: Metadata = {
  title: 'AutoApply',
  description: 'Local web app for the AutoApply pipeline',
};

const NAV = [
  { href: '/', label: 'Dashboard' },
  { href: '/jobs', label: 'Jobs' },
  { href: '/applications', label: 'Applications' },
  { href: '/review', label: 'Review' },
  { href: '/llm', label: 'LLM' },
  { href: '/resumes', label: 'Resumes' },
  { href: '/settings', label: 'Settings' },
  { href: '/pipeline', label: 'Pipeline' },
];

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className="min-h-screen">
        <header className="border-b border-border bg-panel">
          <nav className="mx-auto flex max-w-7xl items-center gap-6 px-6 py-3">
            <Link href="/" className="font-mono text-sm font-semibold tracking-tight">
              autoapply<span className="text-accent">.</span>local
            </Link>
            <ul className="flex flex-1 gap-4 text-sm">
              {NAV.map((item) => (
                <li key={item.href}>
                  <Link
                    href={item.href}
                    className="text-muted hover:text-text transition-colors"
                  >
                    {item.label}
                  </Link>
                </li>
              ))}
            </ul>
            <span className="text-xs text-muted">127.0.0.1 only</span>
          </nav>
        </header>
        <main className="mx-auto max-w-7xl px-6 py-8">{children}</main>
      </body>
    </html>
  );
}
