import Modal from '../common/Modal';

type Props = {
  open: boolean;
  busy?: boolean;
  onSaveAndClose: () => void;
  onDiscard: () => void;
  onCancel: () => void;
};

/**
 * Three-way confirmation when the user closes the composer with
 * unsaved changes — including unpushed attachments (D-28).
 *
 * The lazy-push model means attachments live only in
 * ``draft_attachments`` until the user clicks "Save" or "Send"; closing
 * the composer without warning would lose them. This dialog forces an
 * explicit choice.
 */
export default function CloseComposerDialog({
  open,
  busy,
  onSaveAndClose,
  onDiscard,
  onCancel,
}: Props) {
  return (
    <Modal
      open={open}
      onClose={onCancel}
      ariaLabel="Cambios sin guardar"
      widthClass="max-w-md"
    >
      <div className="space-y-4 px-6 py-5">
        <h2 className="text-base font-semibold text-zinc-900">
          Tienes cambios sin guardar
        </h2>
        <p className="text-[13px] text-zinc-700">
          ¿Qué quieres hacer con este borrador?
        </p>
        <div className="flex flex-wrap items-center justify-end gap-2">
          <button
            type="button"
            disabled={busy}
            onClick={onCancel}
            className="rounded-md border border-zinc-200 px-3 py-1.5 text-[13px] text-zinc-700 hover:bg-zinc-50 disabled:opacity-60"
          >
            Cancelar
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={onDiscard}
            className="rounded-md border border-red-200 bg-white px-3 py-1.5 text-[13px] font-medium text-red-700 hover:bg-red-50 disabled:opacity-60"
          >
            Descartar
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={onSaveAndClose}
            className="rounded-md bg-blue-600 px-3 py-1.5 text-[13px] font-medium text-white hover:bg-blue-700 disabled:opacity-60"
          >
            Guardar y cerrar
          </button>
        </div>
      </div>
    </Modal>
  );
}
