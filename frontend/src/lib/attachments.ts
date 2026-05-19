import blockedExtensionsJson from './blocked_extensions.json';

// D-01: per-file size cap (25 MB). Validated client-side as the first
// line of defence so the user gets immediate feedback without uploading
// the file. Backend re-checks (D-01).
export const MAX_ATTACHMENT_SIZE = 25 * 1024 * 1024;

// D-02: cumulative message size cap (uniform 25 MB across providers).
// The previous 25/35 split disappeared because the asymmetry added
// complexity for ~10 MB of headroom that real users rarely hit.
export const MAX_MESSAGE_SIZE = 25 * 1024 * 1024;

// D-03: max attachments per message. Hard limit, validated client-side
// so the user does not waste an upload that the backend will reject.
export const MAX_ATTACHMENTS_PER_MESSAGE = 25;

// D-04a: extension blocklist mirrored from the backend canonical list
// (backend/core/email/blocked_extensions.py). Sync is manual on
// purpose; a parity test will catch divergence in CI.
export const BLOCKED_EXTENSIONS: ReadonlySet<string> = new Set(blockedExtensionsJson);

export function isBlockedExtension(filename: string): boolean {
  if (!filename) return false;
  const idx = filename.lastIndexOf('.');
  if (idx < 0 || idx === filename.length - 1) return false;
  const extension = filename.slice(idx + 1).toLowerCase();
  return BLOCKED_EXTENSIONS.has(extension);
}

export type ValidationResult =
  | { ok: true }
  | {
      ok: false;
      reason: 'blocked_extension' | 'too_large' | 'message_size_exceeded' | 'limit_exceeded';
      message: string;
    };

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  const mb = bytes / (1024 * 1024);
  return `${mb >= 10 ? mb.toFixed(0) : mb.toFixed(1)} MB`.replace('.', ',');
}

/**
 * Validate a file BEFORE uploading. Mirrors the backend rules so the
 * user gets instant feedback on (a) blocked extensions, (b) over-sized
 * single files, (c) cumulative message size, (d) attachment count cap.
 *
 * The backend re-validates each rule as a safety net (D-04a, D-01,
 * D-02, D-03) so a bug in the frontend never lets the cap leak.
 */
export function validateFileForUpload(
  file: File,
  currentTotalSize: number,
  currentCount: number,
): ValidationResult {
  if (currentCount >= MAX_ATTACHMENTS_PER_MESSAGE) {
    return {
      ok: false,
      reason: 'limit_exceeded',
      message: `No puedes adjuntar más de ${MAX_ATTACHMENTS_PER_MESSAGE} archivos a un mismo correo.`,
    };
  }
  if (isBlockedExtension(file.name)) {
    const idx = file.name.lastIndexOf('.');
    const ext = idx >= 0 ? file.name.slice(idx + 1).toLowerCase() : '';
    return {
      ok: false,
      reason: 'blocked_extension',
      message: `El tipo de archivo .${ext} no se puede enviar por correo.`,
    };
  }
  if (file.size > MAX_ATTACHMENT_SIZE) {
    return {
      ok: false,
      reason: 'too_large',
      message: `El archivo ${file.name} pesa ${formatBytes(file.size)} y supera el límite de 25 MB por archivo.`,
    };
  }
  if (currentTotalSize + file.size > MAX_MESSAGE_SIZE) {
    return {
      ok: false,
      reason: 'message_size_exceeded',
      message: `Añadir ${file.name} superaría el límite de 25 MB total del mensaje.`,
    };
  }
  return { ok: true };
}

/**
 * Map an ApiError ``code`` (from the backend) to a Spanish UI message
 * for attachment-related failures (table in
 * decisionesTomadasAdjuntosFrontend.md §5).
 *
 * Returns ``null`` when the code is not an attachment error so callers
 * can fall back to ``toUiError`` for the generic path.
 */
export function humaniseAttachmentError(
  code: string | undefined,
  detail?: Record<string, unknown>,
): string | null {
  switch (code) {
    case 'attachment_blocked_extension':
      return 'Este tipo de archivo no se puede enviar por correo.';
    case 'attachment_too_large':
      return 'El archivo pesa más de 25 MB.';
    case 'attachment_message_size_exceeded':
      return 'Añadirlo superaría el límite de 25 MB del mensaje.';
    case 'attachment_limit_exceeded':
      return 'No puedes adjuntar más de 25 archivos a un mismo correo.';
    case 'attachment_unavailable':
      return 'Este adjunto ya no está disponible en el servidor.';
    case 'provider_forbidden':
      return 'No se pudo acceder al adjunto.';
    case 'provider_unavailable':
      return 'Inténtalo de nuevo en unos minutos.';
    case 'request_too_large':
      return 'No se pudo subir el archivo.';
    case 'attachment_send_failed': {
      const failed = detail?.failed_attachments;
      if (Array.isArray(failed) && failed.length > 0) {
        return `Algunos adjuntos no se pudieron subir al proveedor (${failed.length}).`;
      }
      return 'El correo no se pudo enviar porque uno o más adjuntos fallaron al subir.';
    }
    default:
      return null;
  }
}
