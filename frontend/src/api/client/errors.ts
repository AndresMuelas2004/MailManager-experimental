import { ZodError } from 'zod';

type ErrorDetail = {
  code: string;
  message: string;
  detail?: Record<string, unknown>;
};

type ErrorResponse = {
  error: ErrorDetail;
};

export type UiError = {
  message: string;
  code?: string;
};

export class ApiError extends Error {
  code: string;
  status?: number;
  detail?: Record<string, unknown>;

  constructor(message: string, code: string, status?: number, detail?: Record<string, unknown>) {
    super(message);
    this.name = 'ApiError';
    this.code = code;
    this.status = status;
    this.detail = detail;
  }
}

export class ValidationError extends ApiError {
  issues: ZodError['issues'];

  constructor(zodError: ZodError) {
    super('Received unexpected data from the server.', 'schema_mismatch');
    this.name = 'ValidationError';
    this.issues = zodError.issues;
  }
}

export function isApiError(value: unknown): value is ApiError {
  return value instanceof ApiError;
}

export function isValidationError(value: unknown): value is ValidationError {
  return value instanceof ValidationError;
}

export function toApiError(status: number, payload?: unknown): ApiError {
  const data = payload as ErrorResponse | undefined;
  if (data?.error?.code) {
    return new ApiError(data.error.message, data.error.code, status, data.error.detail);
  }
  const message = typeof payload === 'string' && payload.length > 0 ? payload : 'Request failed';
  // Preserve a non-enveloped object payload (e.g. FastAPI's ``{ detail: [...] }``
  // validation body) so callers can tell different validation failures apart;
  // the envelope branch above already carries ``error.detail``.
  const detail =
    payload && typeof payload === 'object' ? (payload as Record<string, unknown>) : undefined;
  return new ApiError(message, 'http_error', status, detail);
}

export function toNetworkError(message: string): ApiError {
  return new ApiError(message, 'network_error');
}

export function toUiError(error: unknown): UiError {
  if (error instanceof ApiError) {
    if (error.code === 'rate_limit_exceeded') {
      const secs =
        typeof error.detail?.retry_after === 'number' ? error.detail.retry_after : undefined;
      return {
        code: error.code,
        message: secs
          ? `Demasiadas peticiones. Espera ${secs} segundos e inténtalo de nuevo.`
          : 'Demasiadas peticiones. Espera un momento e inténtalo de nuevo.',
      };
    }
    if (error.code === 'account_not_connected') {
      // The backend ships a developer-facing English message ("...Call /connect
      // first."). Map the CODE to a localized, user-facing string so any surface
      // that renders this error's message shows Spanish instead of the raw text.
      return {
        code: error.code,
        message: 'Una cuenta ha perdido la conexión. Vuelve a conectarla e inténtalo de nuevo.',
      };
    }
    if (error.code === 'account_limit_exceeded') {
      // Second-line defence: the ConnectedAccountsPage already disables "Add
      // account" at the cap, but any path that still reaches the backend 409 gets
      // a localized, user-facing message instead of the raw backend text.
      return {
        code: error.code,
        message: 'Has alcanzado el máximo de cuentas conectadas.',
      };
    }
    if (error.code === 'mailbox_not_found') {
      // The backend message leaks the internal term "Mailbox" and the raw UUID
      // ("Mailbox '...' not found."). Map the CODE to a localized, user-facing
      // string so any listing surface (unified/account inbox, favourites) shows
      // Spanish instead of the raw text.
      return {
        code: error.code,
        message: 'Esta bandeja no existe o ya no está disponible.',
      };
    }
    if (error.code === 'folder_not_found') {
      // Wrapped folder errors — localize the CODE so the folder view / assign
      // menu never show the raw backend text (same pattern as above).
      return {
        code: error.code,
        message: 'Esta carpeta no existe o ya no está disponible.',
      };
    }
    if (error.code === 'folder_name_conflict') {
      return {
        code: error.code,
        message: 'Ya tienes una carpeta con ese nombre.',
      };
    }
    if (error.code === 'rule_not_found') {
      return {
        code: error.code,
        message: 'Esta regla no existe o ya no está disponible.',
      };
    }
    return { message: error.message, code: error.code };
  }
  if (error instanceof Error) {
    return { message: error.message };
  }
  if (typeof error === 'string') {
    return { message: error };
  }
  return { message: 'Unexpected error' };
}
