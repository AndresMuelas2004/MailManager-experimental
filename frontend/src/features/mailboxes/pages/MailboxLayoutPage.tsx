import { useCallback } from 'react';
import { Outlet, useNavigate, useParams } from 'react-router-dom';
import { Inbox, Send, ShieldAlert, FileEdit, Trash2 } from 'lucide-react';
import type { ComponentType } from 'react';

import { useAuth } from '../../../app/providers/AuthContext';
import { useDraftComposerContext } from '../../../app/providers/DraftComposerContext';
import Sidebar from '../../../components/ui/Sidebar';
import DraftComposerHost from '../../drafts/components/DraftComposerHost';
import useMailboxList from '../hooks/useMailboxList';

// Inline because the array is mailbox-feature-only and the features layer's
// "exactly three subdirs" rule (pages / hooks / components) does not allow a
// dedicated constants file. Five entries are not worth a hop.
const MAILBOX_NAV_ITEMS: Array<{
  icon: ComponentType<{ className?: string }>;
  label: string;
  path: string;
}> = [
  { icon: Inbox, label: 'Bandeja unificada', path: 'inbox' },
  { icon: Send, label: 'Enviados', path: 'sent' },
  { icon: ShieldAlert, label: 'Spam', path: 'spam' },
  { icon: FileEdit, label: 'Borradores', path: 'drafts' },
  { icon: Trash2, label: 'Papelera de reciclaje', path: 'trash' },
];

export default function MailboxLayoutPage() {
  const { mailboxId } = useParams<{ mailboxId: string }>();
  if (!mailboxId) return null;
  return <MailboxShell mailboxId={mailboxId} />;
}

function MailboxShell({ mailboxId }: { mailboxId: string }) {
  const navigate = useNavigate();
  const { logout, deleteCurrentUser } = useAuth();
  const { mailboxes, currentMailboxName, handleCreate } = useMailboxList(mailboxId);
  const composer = useDraftComposerContext();

  const handleLogout = useCallback(async () => {
    await logout();
    navigate('/login', { replace: true });
  }, [logout, navigate]);

  const handleDeleteAccount = useCallback(async () => {
    await deleteCurrentUser();
    await logout();
    navigate('/login', { replace: true });
  }, [deleteCurrentUser, logout, navigate]);

  const handleMailboxSelect = useCallback(
    (id: string) => navigate(`/m/${id}/accounts`),
    [navigate],
  );

  const handleMailboxCreate = useCallback(
    async (displayName: string) => {
      const created = await handleCreate(displayName);
      if (created) navigate(`/m/${created.mailbox_id}/accounts`);
    },
    [handleCreate, navigate],
  );

  return (
    <div className="flex min-h-screen bg-[#F9FAFB]">
      <Sidebar
        mailboxId={mailboxId}
        mailboxName={currentMailboxName}
        mailboxes={mailboxes}
        navItems={MAILBOX_NAV_ITEMS}
        onMailboxSelect={handleMailboxSelect}
        onMailboxCreate={handleMailboxCreate}
        onCompose={composer.openForNewEmail}
        onLogout={handleLogout}
        onDeleteAccount={handleDeleteAccount}
      />
      <div className="relative flex-1">
        <Outlet />
      </div>
      <DraftComposerHost mailboxId={mailboxId} />
    </div>
  );
}
