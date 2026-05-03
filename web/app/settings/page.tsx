import Link from 'next/link';
import { getAnswerBank, getCompanies, getEnv, getProfile } from '@/lib/api';
import { PageHeader } from '@/components/ui';
import { ProfileTab } from './ProfileTab';
import { AnswerBankTab } from './AnswerBankTab';
import { CompaniesTab } from './CompaniesTab';
import { EnvTab } from './EnvTab';

export const dynamic = 'force-dynamic';

const TABS = ['profile', 'bank', 'companies', 'env'] as const;
type Tab = (typeof TABS)[number];

type SP = { [k: string]: string | string[] | undefined };
const asStr = (v: SP[string]) => (Array.isArray(v) ? v[0] : v);

export default async function SettingsPage({
  searchParams,
}: {
  searchParams: SP;
}) {
  const tab = (asStr(searchParams.tab) as Tab) ?? 'profile';

  const profile = tab === 'profile' ? await getProfile() : null;
  const bank = tab === 'bank' ? await getAnswerBank() : null;
  const companies = tab === 'companies' ? await getCompanies() : null;
  const env = tab === 'env' ? await getEnv() : null;

  return (
    <div>
      <PageHeader
        title="Settings"
        subtitle="Profile · Answer Bank · Companies · Environment"
      />

      <div className="mb-6 flex gap-2 text-sm">
        {TABS.map((t) => (
          <Link
            key={t}
            href={`?tab=${t}`}
            className={`btn ${tab === t ? 'border-accent text-accent' : ''}`}
          >
            {t === 'bank' ? 'Answer Bank' : t.charAt(0).toUpperCase() + t.slice(1)}
          </Link>
        ))}
      </div>

      {tab === 'profile' && profile ? <ProfileTab data={profile} /> : null}
      {tab === 'bank' && bank ? <AnswerBankTab data={bank} /> : null}
      {tab === 'companies' && companies ? (
        <CompaniesTab data={companies} />
      ) : null}
      {tab === 'env' && env ? <EnvTab data={env} /> : null}
    </div>
  );
}
