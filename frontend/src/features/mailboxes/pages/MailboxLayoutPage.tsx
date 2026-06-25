import { useCallback, useEffect, useState } from 'react';
import { Outlet, useNavigate, useParams } from 'react-router-dom';
import {
  Archive,
  Filter,
  FileEdit,
  Inbox,
  Menu,
  Send,
  ShieldAlert,
  Star,
  Trash2,
} from 'lucide-react';
import type { ComponentType } from 'react';

import { useDraftComposerContext } from '../../../app/providers/DraftComposerContext';
import { useTranslation } from '../../../lib/i18n';
import Sidebar from '../../../components/ui/Sidebar';
import useMailboxList from '../hooks/useMailboxList';
import useMailboxUnreadCounts from '../hooks/useMailboxUnreadCounts';

// Inline because the array is mailbox-feature-only and the features layer's
// "exactly three subdirs" rule (pages / hooks / components) does not allow a
// dedicated constants file. Labels are i18n keys resolved per render.
const MAILBOX_NAV_ITEMS: Array<{
  icon: ComponentType<{ className?: string }>;
  labelKey: string;
  path: string;
}> = [
  { icon: Inbox, labelKey: 'nav.inbox', path: 'inbox' },
  { icon: Send, labelKey: 'nav.sent', path: 'sent' },
  { icon: Star, labelKey: 'nav.favorites', path: 'favorites' },
  { icon: Archive, labelKey: 'nav.archive', path: 'archive' },
  { icon: Filter, labelKey: 'nav.virtualMailboxes', path: 'virtual-mailboxes' },
  { icon: ShieldAlert, labelKey: 'nav.spam', path: 'spam' },
  { icon: FileEdit, labelKey: 'nav.drafts', path: 'drafts' },
  { icon: Trash2, labelKey: 'nav.trash', path: 'trash' },
];

export default function MailboxLayoutPage() {
  const { mailboxId } = useParams<{ mailboxId: string }>();
  if (!mailboxId) return null;
  return <MailboxShell mailboxId={mailboxId} />;
}

function MailboxShell({ mailboxId }: { mailboxId: string }) {
  const navigate = useNavigate();
  const { t } = useTranslation();
  const { mailboxes, currentMailboxName, handleCreate } = useMailboxList(mailboxId);
  const { inboxTotal, spamTotal } = useMailboxUnreadCounts(mailboxId);
  const composer = useDraftComposerContext();

  // Mobile drawer open/close — the only new JS state (legitimate UI state, not
  // server state). On lg: the Sidebar is a static column and this is inert.
  const [drawerOpen, setDrawerOpen] = useState(false);
  const closeDrawer = useCallback(() => setDrawerOpen(false), []);
  // The drawer must close on every navigation so the listing reclaims the full
  // width. The repo's react-hooks lint rules forbid both setState-in-effect and
  // reading refs during render, so the close is driven from the interaction
  // handlers (the React-recommended place for setState) instead of being
  // derived from a location change: Sidebar fires ``onNavigate`` on every
  // NavLink / settings click, and the select/create/compose callbacks below
  // close it too. Tap-outside (backdrop) and the drawer's own X also close it.
  const handleMailboxSelect = useCallback(
    (id: string) => {
      closeDrawer();
      navigate(`/m/${id}/inbox`);
    },
    [navigate, closeDrawer],
  );

  const handleMailboxCreate = useCallback(
    async (displayName: string) => {
      closeDrawer();
      const created = await handleCreate(displayName);
      if (created) navigate(`/m/${created.mailbox_id}/inbox`);
    },
    [handleCreate, navigate, closeDrawer],
  );

  const handleCompose = useCallback(() => {
    closeDrawer();
    composer.openForNewEmail();
  }, [composer, closeDrawer]);

  // Browser tab title reflects the current mailbox's inbox (ALL_MAIL) unread
  // total: ``(N) MailManager`` / ``(99+) MailManager`` / ``MailManager`` at 0.
  // This is the only runtime writer of document.title (index.html ships the
  // static fallback); the cleanup restores it when leaving the mailbox shell.
  useEffect(() => {
    document.title =
      inboxTotal > 0 ? `(${inboxTotal > 99 ? '99+' : inboxTotal}) MailManager` : 'MailManager';
    return () => {
      document.title = 'MailManager';
    };
  }, [inboxTotal]);

  const navItems = MAILBOX_NAV_ITEMS.map(({ icon, labelKey, path }) => ({
    icon,
    label: t(labelKey),
    path,
    badge: path === 'inbox' ? inboxTotal : path === 'spam' ? spamTotal : undefined,
  }));

  return (
    <div className="flex h-screen bg-[#F9FAFB]">
      <Sidebar
        mailboxId={mailboxId}
        mailboxName={currentMailboxName}
        mailboxes={mailboxes}
        navItems={navItems}
        onMailboxSelect={handleMailboxSelect}
        onMailboxCreate={handleMailboxCreate}
        onCompose={handleCompose}
        open={drawerOpen}
        onClose={closeDrawer}
        onNavigate={closeDrawer}
      />

      {/* Dimmed backdrop behind the open drawer (mobile only). */}
      {drawerOpen && (
        <div
          className="fixed inset-0 z-40 bg-black/40 lg:hidden"
          aria-hidden
          onClick={closeDrawer}
        />
      )}

      <div className="flex min-w-0 flex-1 flex-col">
        {/* Mobile top bar: hamburger + current mailbox name. Stays out of the
            scroll flow (shrink-0) so the sticky pager invariant of the Outlet
            scroll container is preserved. Hidden on lg:. */}
        <header className="flex h-14 shrink-0 items-center gap-3 border-b border-zinc-200 bg-white px-4 lg:hidden">
          <button
            type="button"
            onClick={() => setDrawerOpen(true)}
            aria-label={t('nav.openMenu')}
            aria-expanded={drawerOpen}
            aria-controls="mailbox-sidebar"
            className="-ml-1 flex h-10 w-10 items-center justify-center rounded-lg text-zinc-600 hover:bg-zinc-100"
          >
            <Menu className="h-6 w-6" />
          </button>
          <span className="truncate text-sm font-semibold text-zinc-900">
            {currentMailboxName || 'MailManager'}
          </span>
        </header>
        <div className="relative min-w-0 flex-1 overflow-auto">
          <Outlet />
        </div>
      </div>

      {/* Floating compose button (mobile only) — the round compose button lives
          inside the sidebar, which is hidden in the drawer. */}
      <button
        type="button"
        onClick={handleCompose}
        aria-label={t('sidebar.compose')}
        className="fixed bottom-6 right-6 z-30 flex h-14 w-14 items-center justify-center rounded-full bg-blue-600 text-white shadow-lg shadow-blue-600/30 transition-colors hover:bg-blue-700 lg:hidden"
      >
        <Send className="h-6 w-6" />
      </button>
    </div>
  );
}
