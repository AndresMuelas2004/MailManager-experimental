import { useState } from 'react';
import { NavLink, useLocation } from 'react-router-dom';
import { Inbox, Send, Settings, ChevronDown, X } from 'lucide-react';
import type { ComponentType } from 'react';

import MailboxDropdown from './MailboxDropdown';
import Badge from '../common/Badge';
import { useTranslation } from '../../lib/i18n';

type MailboxItem = {
  mailbox_id: string;
  display_name: string | null;
};

type NavItem = {
  icon: ComponentType<{ className?: string }>;
  label: string;
  path: string;
  badge?: number; // nº of unread; the Badge hides itself when 0
};

type Props = {
  mailboxId: string;
  mailboxName: string;
  mailboxes: MailboxItem[];
  navItems: NavItem[];
  onMailboxSelect: (mailboxId: string) => void;
  onMailboxCreate: (displayName: string) => void;
  onMailboxRename: (mailboxId: string, displayName: string) => void;
  onMailboxRequestDelete: (mailbox: MailboxItem) => void;
  onCompose: () => void;
  // Mobile drawer state, owned by MailboxShell. On desktop (lg:) the aside is a
  // static sticky column and these are inert; below lg it slides in/out.
  open: boolean;
  onClose: () => void;
  // Fired when the user taps any navigation link (mailbox section or settings)
  // so the host can close the mobile drawer. The mailbox dropdown and the
  // compose button report through their own callbacks (onMailboxSelect /
  // onMailboxCreate / onCompose), which the host also uses to close the drawer.
  onNavigate: () => void;
};

export default function Sidebar({
  mailboxId,
  mailboxName,
  mailboxes,
  navItems,
  onMailboxSelect,
  onMailboxCreate,
  onMailboxRename,
  onMailboxRequestDelete,
  onCompose,
  open,
  onClose,
  onNavigate,
}: Props) {
  const { t } = useTranslation();
  const [dropdownOpen, setDropdownOpen] = useState(false);
  const base = `/m/${mailboxId}`;
  const { search } = useLocation();

  return (
    <aside
      id="mailbox-sidebar"
      className={`fixed inset-y-0 left-0 z-50 flex h-screen max-h-screen w-[280px] max-w-[85vw] flex-col gap-1 overflow-y-auto border-r border-zinc-200 bg-white px-4 py-6 transition-transform duration-200 ease-out ${open ? 'translate-x-0' : '-translate-x-full'} lg:sticky lg:top-0 lg:z-auto lg:w-[260px] lg:max-w-none lg:translate-x-0 lg:overflow-visible lg:shrink-0 lg:transition-none`}
    >
      <div className="flex items-center gap-2.5 px-2 pb-5">
        <div className="h-8 w-8 shrink-0 overflow-hidden rounded-lg">
          <img src="/logo.png" alt="MailManager" className="h-full w-full object-cover" />
        </div>
        <span className="text-lg font-bold tracking-tight text-zinc-900">MailManager</span>
        <button
          type="button"
          onClick={onClose}
          aria-label={t('common.close')}
          className="ml-auto flex h-9 w-9 items-center justify-center rounded-lg text-zinc-500 hover:bg-zinc-100 lg:hidden"
        >
          <X className="h-5 w-5" />
        </button>
      </div>

      <div className="relative">
        <button
          type="button"
          onClick={() => setDropdownOpen((v) => !v)}
          className="flex h-11 w-full items-center justify-between rounded-[10px] bg-zinc-100 px-3"
        >
          <div className="flex items-center gap-2.5">
            <Inbox className="h-[18px] w-[18px] text-blue-600" />
            <span className="text-sm font-semibold text-zinc-900">
              {mailboxName || t('sidebar.loadingMailbox')}
            </span>
          </div>
          <ChevronDown className="h-4 w-4 text-zinc-500" />
        </button>

        {dropdownOpen && (
          <MailboxDropdown
            mailboxes={mailboxes}
            currentMailboxId={mailboxId}
            onSelect={(id) => {
              setDropdownOpen(false);
              onMailboxSelect(id);
            }}
            onCreate={(name) => {
              setDropdownOpen(false);
              onMailboxCreate(name);
            }}
            onRename={(id, name) => {
              setDropdownOpen(false);
              onMailboxRename(id, name);
            }}
            onRequestDelete={(m) => {
              setDropdownOpen(false);
              onMailboxRequestDelete(m);
            }}
          />
        )}
      </div>

      <nav className="mt-1 flex flex-col gap-0.5">
        {navItems.map(({ icon: Icon, label, path, badge }) => (
          <NavLink
            key={path}
            to={{ pathname: `${base}/${path}`, search }}
            onClick={onNavigate}
            className={({ isActive }) =>
              `flex h-10 items-center gap-3 rounded-lg px-3 text-sm font-medium ${
                isActive ? 'bg-blue-50 text-blue-600' : 'text-zinc-500 hover:bg-zinc-50'
              }`
            }
          >
            <Icon className="h-5 w-5" />
            <span className="flex-1">{label}</span>
            {typeof badge === 'number' && (
              <Badge count={badge} aria-label={t('nav.unreadBadge', { count: badge })} />
            )}
          </NavLink>
        ))}
      </nav>

      <div className="flex-1" />

      <div className="flex justify-center py-4">
        <button
          type="button"
          onClick={onCompose}
          aria-label={t('sidebar.compose')}
          className="flex h-[52px] w-[52px] items-center justify-center rounded-full bg-blue-600 text-white shadow-lg shadow-blue-600/25 transition-colors hover:bg-blue-700"
        >
          <Send className="h-6 w-6" />
        </button>
      </div>

      <div className="px-1 py-2">
        <NavLink
          to={`${base}/settings`}
          onClick={onNavigate}
          aria-label={t('sidebar.settings')}
          className={({ isActive }) =>
            `inline-flex ${isActive ? 'text-blue-600' : 'text-zinc-400 hover:text-zinc-600'}`
          }
        >
          <Settings className="h-5 w-5" />
        </NavLink>
      </div>
    </aside>
  );
}
