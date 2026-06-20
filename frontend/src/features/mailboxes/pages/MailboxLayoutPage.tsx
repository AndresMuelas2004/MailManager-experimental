import { useCallback } from 'react';
import { Outlet, useNavigate, useParams } from 'react-router-dom';
import { Filter, FileEdit, Inbox, Send, ShieldAlert, Star, Trash2 } from 'lucide-react';
import type { ComponentType } from 'react';

import { useDraftComposerContext } from '../../../app/providers/DraftComposerContext';
import { useTranslation } from '../../../lib/i18n';
import Sidebar from '../../../components/ui/Sidebar';
import useMailboxList from '../hooks/useMailboxList';

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
  const composer = useDraftComposerContext();

  const handleMailboxSelect = useCallback((id: string) => navigate(`/m/${id}/inbox`), [navigate]);

  const handleMailboxCreate = useCallback(
    async (displayName: string) => {
      const created = await handleCreate(displayName);
      if (created) navigate(`/m/${created.mailbox_id}/inbox`);
    },
    [handleCreate, navigate],
  );

  const navItems = MAILBOX_NAV_ITEMS.map(({ icon, labelKey, path }) => ({
    icon,
    label: t(labelKey),
    path,
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
        onCompose={composer.openForNewEmail}
      />
      <div className="relative min-w-0 flex-1 overflow-auto">
        <Outlet />
      </div>
    </div>
  );
}
