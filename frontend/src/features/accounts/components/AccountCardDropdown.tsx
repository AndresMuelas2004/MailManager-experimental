import { useState } from 'react';
import { RefreshCw, Trash2 } from 'lucide-react';

import ConfirmModal from '../../../components/common/ConfirmModal';

type Props = {
  onReconnect?: () => void;
  onDelete?: () => void;
};

export default function AccountCardDropdown({ onReconnect, onDelete }: Props) {
  const [confirmDelete, setConfirmDelete] = useState(false);

  return (
    <>
      <div className="absolute right-0 top-full z-30 mt-1 w-56 rounded-xl border border-zinc-200 bg-white py-1 shadow-lg">
        {onReconnect && (
          <button
            type="button"
            onClick={onReconnect}
            className="flex w-full items-center gap-2.5 px-3 py-2.5 text-left text-sm text-zinc-700 hover:bg-zinc-50"
          >
            <RefreshCw className="h-4 w-4" />
            Reconectar cuenta
          </button>
        )}
        {onDelete && (
          <button
            type="button"
            onClick={() => setConfirmDelete(true)}
            className="flex w-full items-center gap-2.5 px-3 py-2.5 text-left text-sm text-red-600 hover:bg-red-50"
          >
            <Trash2 className="h-4 w-4" />
            Eliminar cuenta
          </button>
        )}
      </div>
      {confirmDelete && onDelete && (
        <ConfirmModal
          title="¿Eliminar esta cuenta?"
          description="Se eliminarán los correos y borradores asociados a esta cuenta."
          onCancel={() => setConfirmDelete(false)}
          onConfirm={onDelete}
        />
      )}
    </>
  );
}
