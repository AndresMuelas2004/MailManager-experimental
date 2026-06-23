import { NavLink } from 'react-router-dom';
import { Inbox, Send, Star, ShieldAlert, FileEdit, Trash2 } from 'lucide-react';
import type { ComponentType } from 'react';

import { useTranslation } from '../../lib/i18n';

type TabDef = {
  to: string;
  label: string;
  icon: ComponentType<{ className?: string }>;
};

type Props = {
  basePath: string;
  inboxLabel?: string;
};

export default function AccountTabs({ basePath, inboxLabel }: Props) {
  const { t } = useTranslation();
  const tabs: TabDef[] = [
    { to: `${basePath}/inbox`, label: inboxLabel ?? t('nav.mailbox'), icon: Inbox },
    { to: `${basePath}/sent`, label: t('nav.sent'), icon: Send },
    { to: `${basePath}/favorites`, label: t('nav.favorites'), icon: Star },
    { to: `${basePath}/spam`, label: t('nav.spam'), icon: ShieldAlert },
    { to: `${basePath}/drafts`, label: t('drafts.title'), icon: FileEdit },
    { to: `${basePath}/trash`, label: t('virtualMailboxes.boxTrash'), icon: Trash2 },
  ];

  return (
    <div className="flex gap-1 overflow-x-auto border-b border-zinc-200 px-4 lg:px-8">
      {tabs.map(({ to, label, icon: Icon }) => (
        <NavLink
          key={to}
          to={to}
          className={({ isActive }) =>
            `flex shrink-0 items-center gap-1.5 border-b-2 px-3 py-2.5 text-sm font-medium whitespace-nowrap transition-colors ${
              isActive
                ? 'border-blue-600 text-blue-600'
                : 'border-transparent text-zinc-500 hover:text-zinc-700'
            }`
          }
        >
          <Icon className="h-4 w-4" />
          {label}
        </NavLink>
      ))}
    </div>
  );
}
