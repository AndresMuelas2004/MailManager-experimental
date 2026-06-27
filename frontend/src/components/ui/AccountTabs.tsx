import { NavLink, useLocation } from 'react-router-dom';
import { Inbox, Send, Star, ShieldAlert, FileEdit, Trash2 } from 'lucide-react';
import type { ComponentType } from 'react';

import Badge from '../common/Badge';
import { useTranslation } from '../../lib/i18n';
import { navSearchResettingControls } from '../../lib/listControls';

type TabDef = {
  to: string;
  label: string;
  icon: ComponentType<{ className?: string }>;
  badge?: number; // nº of unread; the Badge hides itself when 0
};

type Props = {
  basePath: string;
  inboxLabel?: string;
  inboxUnread?: number;
  spamUnread?: number;
};

export default function AccountTabs({ basePath, inboxLabel, inboxUnread, spamUnread }: Props) {
  const { t } = useTranslation();
  // Switching box section preserves the lupa ``q`` term across sections
  // (buzones-y-vista-unificada §2.3) but resets the sort/filter controls
  // (ordenar-y-filtrar §4.7) and pagination to page 1 (listado-de-correos
  // §7.1), since the box context changed.
  const { search } = useLocation();
  const navSearch = navSearchResettingControls(search);
  const tabs: TabDef[] = [
    {
      to: `${basePath}/inbox`,
      label: inboxLabel ?? t('nav.mailbox'),
      icon: Inbox,
      badge: inboxUnread,
    },
    { to: `${basePath}/sent`, label: t('nav.sent'), icon: Send },
    { to: `${basePath}/favorites`, label: t('nav.favorites'), icon: Star },
    { to: `${basePath}/spam`, label: t('nav.spam'), icon: ShieldAlert, badge: spamUnread },
    { to: `${basePath}/drafts`, label: t('drafts.title'), icon: FileEdit },
    { to: `${basePath}/trash`, label: t('virtualMailboxes.boxTrash'), icon: Trash2 },
  ];

  return (
    <div className="flex shrink-0 gap-1 overflow-x-auto border-b border-zinc-200 px-4 lg:px-8">
      {tabs.map(({ to, label, icon: Icon, badge }) => (
        <NavLink
          key={to}
          to={{ pathname: to, search: navSearch }}
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
          {typeof badge === 'number' && (
            <Badge count={badge} aria-label={t('nav.unreadBadge', { count: badge })} />
          )}
        </NavLink>
      ))}
    </div>
  );
}
