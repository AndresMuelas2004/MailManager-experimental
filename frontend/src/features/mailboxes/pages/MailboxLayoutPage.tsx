import { useCallback, useEffect, useState } from 'react';
import { Outlet, useMatch, useNavigate, useParams } from 'react-router-dom';
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
import ConfirmModal from '../../../components/common/ConfirmModal';
import useMailboxList from '../hooks/useMailboxList';
import useMailboxAccounts from '../hooks/useMailboxAccounts';
import useMailboxUnreadCounts from '../hooks/useMailboxUnreadCounts';
import useRenameMailbox from '../hooks/useRenameMailbox';
import useDeleteMailbox from '../hooks/useDeleteMailbox';

// Inline because the array is mailbox-feature-only and the features layer's
// "exactly three subdirs" rule (pages / hooks / components) does not allow a
// dedicated constants file. Labels are i18n keys resolved per render. ``global``
// marks entries that have no per-account route, so the Sidebar keeps them on the
// unified base even inside an account scope (Bandejas ficticias).
const MAILBOX_NAV_ITEMS: Array<{
  icon: ComponentType<{ className?: string }>;
  labelKey: string;
  path: string;
  global?: boolean;
}> = [
  { icon: Inbox, labelKey: 'nav.inbox', path: 'inbox' },
  { icon: Send, labelKey: 'nav.sent', path: 'sent' },
  { icon: Star, labelKey: 'nav.favorites', path: 'favorites' },
  { icon: Archive, labelKey: 'nav.archive', path: 'archive' },
  { icon: Filter, labelKey: 'nav.virtualMailboxes', path: 'virtual-mailboxes', global: true },
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
  const { accounts } = useMailboxAccounts(mailboxId);
  const { inboxTotal, spamTotal, inboxByAccount, spamByAccount } =
    useMailboxUnreadCounts(mailboxId);
  const { rename: renameMailbox } = useRenameMailbox();
  const { remove: removeMailbox } = useDeleteMailbox();
  const composer = useDraftComposerContext();

  // Active scope derived from the URL: an /account/:accountId/* route means a
  // single-account scope, anything else is the unified ("Todas") scope.
  const accountMatch = useMatch('/m/:mailboxId/account/:accountId/*');
  const activeAccountId = accountMatch?.params.accountId ?? null;

  // Confirmation for the header-selector delete entry point. The selector only
  // offers delete for the active mailbox, so storing its id is enough.
  const [pendingDeleteId, setPendingDeleteId] = useState<string | null>(null);

  // Mobile drawer open/close — the only new JS state (legitimate UI state, not
  // server state). On lg: the Sidebar is a static column and this is inert.
  const [drawerOpen, setDrawerOpen] = useState(false);
  const closeDrawer = useCallback(() => setDrawerOpen(false), []);
  // The drawer must close on every navigation so the listing reclaims the full
  // width. The repo's react-hooks lint rules forbid both setState-in-effect and
  // reading refs during render, so the close is driven from the interaction
  // handlers (the React-recommended place for setState) instead of being
  // derived from a location change: Sidebar fires ``onNavigate`` on every
  // NavLink / scope / settings click, and the select/create/compose callbacks
  // below close it too. Tap-outside (backdrop) and the drawer's own X also close it.
  const handleMailboxSelect = useCallback(
    (id: string) => {
      closeDrawer();
      navigate(`/m/${id}/inbox`);
    },
    [navigate, closeDrawer],
  );

  // Scope switch: navigate to the Inbox of the chosen scope (a specific account,
  // or the unified mailbox when accountId is null). The folders then follow.
  const handleScopeSelect = useCallback(
    (accountId: string | null) => {
      closeDrawer();
      navigate(accountId ? `/m/${mailboxId}/account/${accountId}/inbox` : `/m/${mailboxId}/inbox`);
    },
    [navigate, mailboxId, closeDrawer],
  );

  const handleMailboxCreate = useCallback(
    async (displayName: string) => {
      closeDrawer();
      const created = await handleCreate(displayName);
      if (created) navigate(`/m/${created.mailbox_id}/settings/accounts`);
    },
    [handleCreate, navigate, closeDrawer],
  );

  const handleMailboxRename = useCallback(
    (id: string, displayName: string) => {
      void renameMailbox({ mailboxId: id, displayName });
    },
    [renameMailbox],
  );

  const handleMailboxRequestDelete = useCallback(
    (mailbox: { mailbox_id: string }) => {
      closeDrawer();
      setPendingDeleteId(mailbox.mailbox_id);
    },
    [closeDrawer],
  );

  const handleConfirmDelete = async () => {
    if (!pendingDeleteId) return;
    const deletedId = pendingDeleteId;
    const ok = await removeMailbox(deletedId);
    setPendingDeleteId(null);
    if (!ok) return;

    // The selector only deletes the active mailbox, so the user is always
    // viewing the one just removed: move them to a surviving mailbox's inbox,
    // or to the index (which routes to "create mailbox") when none remain.
    if (deletedId === mailboxId) {
      const survivor = mailboxes.find((m) => m.mailbox_id !== deletedId);
      if (survivor) navigate(`/m/${survivor.mailbox_id}/inbox`);
      else navigate('/');
    }
  };

  const handleCompose = useCallback(() => {
    closeDrawer();
    composer.openForNewEmail();
  }, [composer, closeDrawer]);

  // Browser tab title reflects the current mailbox's inbox (ALL_MAIL) unread
  // total: ``(N) MISSELA`` / ``(99+) MISSELA`` / ``MISSELA`` at 0.
  // Mailbox-wide (not scope-narrowed) on purpose — the tab represents the whole
  // mailbox. This is the only runtime writer of document.title (index.html ships
  // the static fallback); the cleanup restores it when leaving the mailbox shell.
  useEffect(() => {
    document.title =
      inboxTotal > 0 ? `(${inboxTotal > 99 ? '99+' : inboxTotal}) MISSELA` : 'MISSELA';
    return () => {
      document.title = 'MISSELA';
    };
  }, [inboxTotal]);

  // Nav badges follow the active scope: an account scope shows that account's
  // unread from the breakdown; the unified scope shows the mailbox-wide total.
  const inboxBadge = activeAccountId ? (inboxByAccount.get(activeAccountId) ?? 0) : inboxTotal;
  const spamBadge = activeAccountId ? (spamByAccount.get(activeAccountId) ?? 0) : spamTotal;

  const navItems = MAILBOX_NAV_ITEMS.map(({ icon, labelKey, path, global }) => ({
    icon,
    // The inbox entry reads "Bandeja unificada" in the unified scope but
    // "Bandeja de entrada" inside a single account (where nothing is unified).
    label: path === 'inbox' && activeAccountId ? t('nav.inboxAccount') : t(labelKey),
    path,
    global,
    badge: path === 'inbox' ? inboxBadge : path === 'spam' ? spamBadge : undefined,
  }));

  return (
    <div className="flex h-screen bg-[#F9FAFB]">
      <Sidebar
        mailboxId={mailboxId}
        mailboxName={currentMailboxName}
        mailboxes={mailboxes}
        accounts={accounts}
        activeAccountId={activeAccountId}
        navItems={navItems}
        onMailboxSelect={handleMailboxSelect}
        onMailboxCreate={handleMailboxCreate}
        onMailboxRename={handleMailboxRename}
        onMailboxRequestDelete={handleMailboxRequestDelete}
        onScopeSelect={handleScopeSelect}
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
            {currentMailboxName || 'MISSELA'}
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

      {pendingDeleteId && (
        <ConfirmModal
          title={t('mailboxesSettings.confirmDeleteTitle')}
          description={t('mailboxesSettings.confirmDeleteDescription')}
          confirmLabel={t('common.delete')}
          cancelLabel={t('common.cancel')}
          onCancel={() => setPendingDeleteId(null)}
          onConfirm={handleConfirmDelete}
        />
      )}
    </div>
  );
}
