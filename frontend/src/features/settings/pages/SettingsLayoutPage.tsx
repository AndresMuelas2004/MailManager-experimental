import { NavLink, Outlet, useParams } from 'react-router-dom';
import {
  Database,
  Info,
  Inbox,
  Languages,
  Link as LinkIcon,
  Signature,
  User,
  Wand2,
} from 'lucide-react';
import type { ComponentType } from 'react';

import { useTranslation } from '../../../lib/i18n';

type SectionDef = {
  to: string;
  labelKey: string;
  icon: ComponentType<{ className?: string }>;
  // The "Tu cuenta" index route must match exactly, otherwise it stays active
  // for every nested settings path.
  end?: boolean;
};

const SECTIONS: SectionDef[] = [
  { to: '', labelKey: 'settings.navAccount', icon: User, end: true },
  { to: 'accounts', labelKey: 'settings.navConnectedAccounts', icon: LinkIcon },
  { to: 'signature', labelKey: 'settings.navSignature', icon: Signature },
  { to: 'mailboxes', labelKey: 'settings.navMailboxes', icon: Inbox },
  { to: 'rules', labelKey: 'settings.navRules', icon: Wand2 },
  { to: 'preferences', labelKey: 'settings.navLanguage', icon: Languages },
  { to: 'data', labelKey: 'settings.navData', icon: Database },
  { to: 'about', labelKey: 'settings.navAbout', icon: Info },
];

export default function SettingsLayoutPage() {
  const { mailboxId } = useParams<{ mailboxId: string }>();
  const { t } = useTranslation();
  const base = `/m/${mailboxId}/settings`;

  return (
    <div className="flex h-full min-h-0 flex-col lg:flex-row">
      <nav className="flex gap-1 overflow-x-auto border-b border-zinc-200 bg-white px-2 py-2 lg:w-[240px] lg:shrink-0 lg:flex-col lg:overflow-visible lg:border-b-0 lg:border-r lg:px-3 lg:py-6">
        <h2 className="hidden px-3 pb-3 text-lg font-bold tracking-tight text-zinc-900 lg:block">
          {t('settings.title')}
        </h2>
        {SECTIONS.map(({ to, labelKey, icon: Icon, end }) => (
          <NavLink
            key={to || 'index'}
            to={to ? `${base}/${to}` : base}
            end={end}
            className={({ isActive }) =>
              `flex h-10 shrink-0 items-center gap-3 rounded-lg px-3 text-sm font-medium whitespace-nowrap ${
                isActive ? 'bg-blue-50 text-blue-600' : 'text-zinc-600 hover:bg-zinc-50'
              }`
            }
          >
            <Icon className="h-[18px] w-[18px]" />
            {t(labelKey)}
          </NavLink>
        ))}
      </nav>

      <div className="min-w-0 flex-1 overflow-auto">
        <Outlet />
      </div>
    </div>
  );
}
