import { useEffect, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { X } from 'lucide-react';

import { useTranslation } from '../../lib/i18n';

type Props = {
  open: boolean;
  onClose: () => void;
  children: ReactNode;
  ariaLabel?: string;
  widthClass?: string;
  // When true, the modal fills the screen below lg: (no padding, square
  // corners, full height) and reverts to the centred floating card on lg:.
  // Used by full-screen viewers (email / conversation) and the long virtual
  // mailbox form. Small confirmation dialogs leave it off and stay centred.
  mobileFullScreen?: boolean;
};

export default function Modal({
  open,
  onClose,
  children,
  ariaLabel,
  widthClass = 'max-w-4xl',
  mobileFullScreen = false,
}: Props) {
  const { t } = useTranslation();

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  useEffect(() => {
    if (!open) return;
    const { overflow } = document.body.style;
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = overflow;
    };
  }, [open]);

  if (!open) return null;

  const overlayClass = mobileFullScreen
    ? 'fixed inset-0 z-50 flex items-stretch justify-center bg-black/50 p-0 lg:items-center lg:p-6'
    : 'fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4 lg:p-6';
  const panelShape = mobileFullScreen
    ? 'h-full max-h-full rounded-none lg:h-auto lg:max-h-[90vh] lg:rounded-2xl'
    : 'max-h-[90vh] rounded-2xl';

  return createPortal(
    <div
      role="dialog"
      aria-modal="true"
      aria-label={ariaLabel}
      className={overlayClass}
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        className={`relative flex w-full flex-col overflow-hidden bg-white shadow-2xl ${panelShape} ${widthClass}`}
      >
        <button
          type="button"
          onClick={onClose}
          aria-label={t('common.close')}
          className="absolute top-4 right-4 z-10 flex h-8 w-8 items-center justify-center rounded-full text-zinc-500 transition-colors hover:bg-zinc-100 hover:text-zinc-700"
        >
          <X className="h-5 w-5" />
        </button>
        {children}
      </div>
    </div>,
    document.body,
  );
}
