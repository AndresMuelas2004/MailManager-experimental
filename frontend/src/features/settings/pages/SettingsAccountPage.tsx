import { useCallback, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { LogOut, Trash2 } from 'lucide-react';

import { useAuth } from '../../../app/providers/AuthContext';
import { useTranslation } from '../../../lib/i18n';
import IdentityHeader from '../components/IdentityHeader';
import DeleteUserDialog from '../components/DeleteUserDialog';

export default function SettingsAccountPage() {
  const navigate = useNavigate();
  const { t } = useTranslation();
  const { user, logout, deleteCurrentUser } = useAuth();
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deleting, setDeleting] = useState(false);

  // Logging out must always land on /login (never "create mailbox") — the
  // destination that the previous SettingsDropdown flow guaranteed and that
  // this section preserves.
  const handleLogout = useCallback(async () => {
    await logout();
    navigate('/login', { replace: true });
  }, [logout, navigate]);

  const handleConfirmDelete = useCallback(async () => {
    setDeleting(true);
    // Parity with the previous flow: delete then log out then go to /login.
    // The boolean from deleteCurrentUser is intentionally not branched on — the
    // session is cleared and the user returns to login regardless.
    await deleteCurrentUser();
    await logout();
    navigate('/login', { replace: true });
  }, [deleteCurrentUser, logout, navigate]);

  if (!user) return null;

  return (
    <div className="flex flex-col gap-8 px-4 pt-6 pb-6 lg:px-8 lg:pt-8">
      <div className="flex flex-col gap-2">
        <h1 className="text-2xl font-bold tracking-tight text-zinc-900 lg:text-[28px]">
          {t('settings.account.title')}
        </h1>
        <p className="text-[15px] leading-[1.5] text-zinc-500">{t('settings.account.subtitle')}</p>
      </div>

      <IdentityHeader user={user} />

      <div className="flex flex-col gap-3 border-t border-zinc-100 pt-6">
        <button
          type="button"
          onClick={handleLogout}
          className="inline-flex w-fit items-center gap-2 rounded-lg border border-zinc-200 bg-white px-4 py-2 text-sm font-medium text-zinc-700 hover:bg-zinc-50"
        >
          <LogOut className="h-4 w-4" />
          {t('settings.account.logout')}
        </button>
        <button
          type="button"
          onClick={() => setDeleteOpen(true)}
          className="inline-flex w-fit items-center gap-2 rounded-lg border border-red-200 bg-white px-4 py-2 text-sm font-medium text-red-600 hover:bg-red-50"
        >
          <Trash2 className="h-4 w-4" />
          {t('settings.account.deleteAccount')}
        </button>
      </div>

      <DeleteUserDialog
        open={deleteOpen}
        email={user.email}
        busy={deleting}
        onConfirm={handleConfirmDelete}
        onClose={() => setDeleteOpen(false)}
      />
    </div>
  );
}
