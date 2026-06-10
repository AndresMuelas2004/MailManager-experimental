import type { ZodType, ZodTypeDef } from 'zod';
import { ApiError, ValidationError, toApiError, toNetworkError } from './errors';

export type BlobDownload = { blob: Blob; filename: string };

const DEFAULT_BASE_URL = 'http://localhost:8000';
const DEFAULT_HEADERS: HeadersInit = {
  Accept: 'application/json',
};

type RequestOptions<T> = {
  method?: string;
  headers?: HeadersInit;
  body?: unknown;
  signal?: AbortSignal;
  // Decouple the schema's Input type (third generic) from its Output (T).
  // Schemas that use ``.default(...)`` have an optional Input but a required
  // Output; binding ``ZodType<T>`` (where Input defaults to Output) made TS
  // infer T from the *Input*, so the returned value (the parsed Output) did
  // not match the endpoint's declared ``z.infer`` return type. Fixing Input
  // to ``unknown`` forces T to be inferred from the Output, which is exactly
  // what ``safeParse`` returns.
  schema?: ZodType<T, ZodTypeDef, unknown>;
};

function getBaseUrl(): string {
  const envBase =
    typeof import.meta !== 'undefined'
      ? (import.meta.env?.VITE_API_BASE_URL as string | undefined)
      : undefined;
  return envBase && envBase.length > 0 ? envBase : DEFAULT_BASE_URL;
}

function buildUrl(path: string): string {
  const base = getBaseUrl().replace(/\/+$/, '');
  const normalized = path.startsWith('/') ? path : `/${path}`;
  return `${base}${normalized}`;
}

/**
 * Browser origin of the API backend. The OAuth connect flow validates
 * `postMessage` events from the callback popup against this origin.
 */
export function getApiOrigin(): string {
  return new URL(getBaseUrl()).origin;
}

async function parseBody(response: Response): Promise<unknown> {
  if (response.status === 204) {
    return undefined;
  }
  const contentType = response.headers.get('content-type') || '';
  if (contentType.includes('application/json')) {
    return response.json();
  }
  const text = await response.text();
  return text.length > 0 ? text : undefined;
}

export async function request<T>(path: string, options: RequestOptions<T> = {}): Promise<T> {
  const url = buildUrl(path);
  const headers: HeadersInit = {
    ...DEFAULT_HEADERS,
    ...options.headers,
  };

  const init: RequestInit = {
    method: options.method ?? 'GET',
    headers,
    signal: options.signal,
    credentials: 'include',
  };

  if (options.body !== undefined) {
    (init.headers as Record<string, string>)['Content-Type'] = 'application/json';
    init.body = JSON.stringify(options.body);
  }

  let response: Response;
  let data: unknown;
  try {
    response = await fetch(url, init);
    data = await parseBody(response);
  } catch (error) {
    if (error instanceof ApiError) {
      throw error;
    }
    throw toNetworkError('Network error while contacting the API.');
  }

  if (!response.ok) {
    throw toApiError(response.status, data);
  }

  if (options.schema) {
    const parsed = options.schema.safeParse(data);
    if (!parsed.success) {
      throw new ValidationError(parsed.error);
    }
    return parsed.data;
  }

  return data as T;
}

function parseFilenameFromContentDisposition(headerValue: string | null): string | null {
  if (!headerValue) return null;
  const utf8 = /filename\*\s*=\s*UTF-8''([^;]+)/i.exec(headerValue);
  if (utf8) {
    try {
      return decodeURIComponent(utf8[1]);
    } catch {
      // fall through to the legacy form
    }
  }
  const ascii = /filename\s*=\s*"([^"]+)"/i.exec(headerValue);
  if (ascii) return ascii[1];
  return null;
}

/**
 * Binary download companion to `request<T>()`. Resolves to a Blob and the
 * filename declared by the ``Content-Disposition`` header. Required because
 * `request<T>()` always parses the response body as JSON/text — it cannot
 * stream binaries. Uses the same `credentials: "include"` policy and the
 * same `ApiError` translation pipeline.
 *
 * Per §3.1 of api/CLAUDE.md, this is the second (and final) place in the
 * codebase where `fetch()` may appear directly. Endpoints layer wrappers
 * MUST funnel binary downloads through this helper rather than calling
 * `fetch()` themselves.
 */
export async function requestBlob(
  path: string,
  options: { method?: 'GET' | 'POST'; signal?: AbortSignal; fallbackFilename?: string } = {},
): Promise<BlobDownload> {
  const url = buildUrl(path);
  let response: Response;
  try {
    response = await fetch(url, {
      method: options.method ?? 'GET',
      credentials: 'include',
      signal: options.signal,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw new ApiError('Download cancelled.', 'upload_cancelled');
    }
    throw toNetworkError('Network error while downloading binary.');
  }

  if (!response.ok) {
    let payload: unknown;
    try {
      payload = await response.json();
    } catch {
      payload = await response.text().catch(() => undefined);
    }
    throw toApiError(response.status, payload);
  }

  const blob = await response.blob();
  const filename =
    parseFilenameFromContentDisposition(response.headers.get('content-disposition')) ??
    options.fallbackFilename ??
    'download';
  return { blob, filename };
}

/**
 * Multipart upload with real progress reporting (D-25).
 *
 * `fetch()` does not expose upload progress, so we drop down to
 * `XMLHttpRequest` here. The function preserves the same contract as
 * `request<T>()`: same `credentials: include`, same Zod validation on
 * the response body, same `ApiError` / `ValidationError` outputs.
 *
 * **Per the api/CLAUDE.md §3.1 rule, this is the ONLY place in the
 * codebase that may instantiate `XMLHttpRequest`.** Components and
 * hooks call this wrapper instead of touching XHR directly.
 */
export type UploadProgressOptions<T> = {
  onProgress?: (percentage: number) => void;
  signal?: AbortSignal;
  // See RequestOptions.schema: Input fixed to ``unknown`` so T is inferred
  // from the schema's Output (what ``safeParse`` returns), not its Input.
  schema: ZodType<T, ZodTypeDef, unknown>;
};

export function requestUploadWithProgress<T>(
  path: string,
  formData: FormData,
  options: UploadProgressOptions<T>,
): Promise<T> {
  const url = buildUrl(path);
  return new Promise<T>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', url);
    xhr.withCredentials = true;
    xhr.responseType = 'json';

    if (options.onProgress) {
      const onProgress = options.onProgress;
      xhr.upload.onprogress = (event) => {
        if (event.lengthComputable && event.total > 0) {
          onProgress(Math.round((event.loaded / event.total) * 100));
        }
      };
    }

    if (options.signal) {
      const onAbort = () => {
        try {
          xhr.abort();
        } catch {
          // already aborted; nothing useful to do
        }
      };
      if (options.signal.aborted) {
        onAbort();
      } else {
        options.signal.addEventListener('abort', onAbort, { once: true });
      }
    }

    xhr.onload = () => {
      const status = xhr.status;
      const body: unknown = xhr.response;
      if (status < 200 || status >= 300) {
        reject(toApiError(status, body));
        return;
      }
      const parsed = options.schema.safeParse(body);
      if (!parsed.success) {
        reject(new ValidationError(parsed.error));
        return;
      }
      resolve(parsed.data);
    };

    xhr.onerror = () => {
      reject(toNetworkError('Network error during upload.'));
    };

    xhr.onabort = () => {
      reject(new ApiError('Upload cancelled.', 'upload_cancelled'));
    };

    xhr.send(formData);
  });
}
