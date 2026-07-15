import { useState } from 'react';
import { NavLink, useLocation } from 'react-router-dom';
import { Check, ChevronDown, Inbox, Layers, Mail, Send, Settings, X } from 'lucide-react';
import type { ComponentType } from 'react';

import MailboxDropdown from './MailboxDropdown';
import Badge from '../common/Badge';
import { getProviderMeta } from '../../lib/providers';
import { useTranslation } from '../../lib/i18n';
import { navSearchResettingControls } from '../../lib/listControls';

type MailboxItem = {
  mailbox_id: string;
  display_name: string | null;
};

type AccountItem = {
  account_id: string;
  provider: string;
  display_label: string;
  email_address: string | null;
};

type NavItem = {
  icon: ComponentType<{ className?: string }>;
  label: string;
  path: string;
  badge?: number; // nº of unread; the Badge hides itself when 0
  // When true the link always targets the unified base (/m/:mailboxId/...),
  // ignoring the active account scope — for entries that have no per-account
  // route (Bandejas ficticias / virtual mailboxes).
  global?: boolean;
};

type Props = {
  mailboxId: string;
  mailboxName: string;
  mailboxes: MailboxItem[];
  accounts: AccountItem[];
  // null = unified scope ("Todas"); otherwise the account whose individual
  // views the folders point at. Derived from the URL by the host page.
  activeAccountId: string | null;
  navItems: NavItem[];
  onMailboxSelect: (mailboxId: string) => void;
  onMailboxCreate: (displayName: string) => void;
  onMailboxRename: (mailboxId: string, displayName: string) => void;
  onMailboxRequestDelete: (mailbox: MailboxItem) => void;
  onScopeSelect: (accountId: string | null) => void;
  onCompose: () => void;
  // Mobile drawer state, owned by MailboxShell. On desktop (lg:) the aside is a
  // static sticky column and these are inert; below lg it slides in/out.
  open: boolean;
  onClose: () => void;
  // Fired when the user taps any navigation link (mailbox section or settings)
  // so the host can close the mobile drawer. The mailbox dropdown, the scope
  // switcher and the compose button report through their own callbacks, which
  // the host also uses to close the drawer.
  onNavigate: () => void;
};

export default function Sidebar({
  mailboxId,
  mailboxName,
  mailboxes,
  accounts,
  activeAccountId,
  navItems,
  onMailboxSelect,
  onMailboxCreate,
  onMailboxRename,
  onMailboxRequestDelete,
  onScopeSelect,
  onCompose,
  open,
  onClose,
  onNavigate,
}: Props) {
  const { t } = useTranslation();
  const [dropdownOpen, setDropdownOpen] = useState(false);
  const [accountsOpen, setAccountsOpen] = useState(false);
  const activeAccount = accounts.find((a) => a.account_id === activeAccountId) ?? null;
  const unifiedBase = `/m/${mailboxId}`;
  // Folder links follow the active scope: an account scope points folders at
  // that account's routes; the unified scope at the mailbox routes. Entries
  // flagged ``global`` always use the unified base (no per-account route).
  const scopeBase = activeAccountId ? `${unifiedBase}/account/${activeAccountId}` : unifiedBase;
  const { search } = useLocation();
  // Switching box section preserves the lupa ``q`` term across sections
  // (buzones-y-vista-unificada §2.3) but resets the sort/filter controls
  // (ordenar-y-filtrar §4.7) and pagination to page 1 (listado-de-correos
  // §7.1), since the box context changed.
  const navSearch = navSearchResettingControls(search);

  return (
    <aside
      id="mailbox-sidebar"
      className={`fixed inset-y-0 left-0 z-50 flex h-screen max-h-screen w-[280px] max-w-[85vw] flex-col gap-1 overflow-y-auto border-r border-zinc-200 bg-white px-4 py-6 transition-transform duration-200 ease-out ${open ? 'translate-x-0' : '-translate-x-full'} lg:sticky lg:top-0 lg:z-auto lg:w-[260px] lg:max-w-none lg:translate-x-0 lg:overflow-y-auto lg:shrink-0 lg:transition-none`}
    >
      <div className="flex items-center gap-2.5 px-2 pb-5">
        <div className="h-8 w-8 shrink-0 overflow-hidden rounded-lg">
          <img src="/logo.png" alt="MISSELA" className="h-full w-full object-cover" />
        </div>
        <span className="text-lg font-bold tracking-tight text-zinc-900">MISSELA</span>
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

      {/* Account scope switcher (collapsible). The trigger shows the active
          scope; picking an entry switches scope, navigates to its Inbox and
          closes the list. Accounts stay hidden until the trigger is opened. */}
      <div className="relative mt-3">
        <button
          type="button"
          onClick={() => setAccountsOpen((v) => !v)}
          aria-expanded={accountsOpen}
          className="flex h-11 w-full items-center justify-between gap-2 rounded-[10px] bg-zinc-50 px-3"
        >
          <div className="flex min-w-0 items-center gap-2.5">
            {activeAccount ? (
              <Mail
                className={`h-[18px] w-[18px] shrink-0 ${getProviderMeta(activeAccount.provider).headerTextClass}`}
              />
            ) : (
              <Layers className="h-[18px] w-[18px] shrink-0 text-blue-600" />
            )}
            <span className="truncate text-sm font-medium text-zinc-900">
              {activeAccount
                ? (activeAccount.email_address ?? activeAccount.display_label)
                : t('sidebar.allAccounts')}
            </span>
          </div>
          <ChevronDown className="h-4 w-4 shrink-0 text-zinc-500" />
        </button>

        {accountsOpen && (
          <div className="absolute left-0 right-0 top-full z-20 mt-1 rounded-xl border border-zinc-200 bg-white py-1 shadow-lg">
            <button
              type="button"
              onClick={() => {
                setAccountsOpen(false);
                onScopeSelect(null);
                onNavigate();
              }}
              className="flex w-full items-center gap-2.5 px-3 py-2 text-left text-sm hover:bg-zinc-50"
            >
              <Layers className="h-4 w-4 shrink-0 text-blue-600" />
              <span className="flex-1 truncate text-zinc-900">{t('sidebar.allAccounts')}</span>
              {activeAccountId === null && <Check className="h-4 w-4 shrink-0 text-blue-600" />}
            </button>
            {accounts.map((acc) => {
              const active = acc.account_id === activeAccountId;
              const meta = getProviderMeta(acc.provider);
              return (
                <button
                  key={acc.account_id}
                  type="button"
                  onClick={() => {
                    setAccountsOpen(false);
                    onScopeSelect(acc.account_id);
                    onNavigate();
                  }}
                  className="flex w-full items-center gap-2.5 px-3 py-2 text-left text-sm hover:bg-zinc-50"
                >
                  <Mail className={`h-4 w-4 shrink-0 ${meta.headerTextClass}`} />
                  <span className="flex-1 truncate text-zinc-900">
                    {acc.email_address ?? acc.display_label}
                  </span>
                  {active && <Check className="h-4 w-4 shrink-0 text-blue-600" />}
                </button>
              );
            })}
          </div>
        )}
      </div>

      <nav className="mt-2 flex flex-col gap-0.5">
        {navItems.map(({ icon: Icon, label, path, badge, global }) => (
          <NavLink
            key={path}
            to={{ pathname: `${global ? unifiedBase : scopeBase}/${path}`, search: navSearch }}
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
          to={`${unifiedBase}/settings`}
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
