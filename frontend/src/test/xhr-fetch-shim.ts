/**
 * Test-only `XMLHttpRequest` shim that delegates to `fetch`.
 *
 * MSW v2's XHR interceptor intercepts the request in this jsdom + Vitest
 * runtime (the handler runs) but never delivers the response back to the
 * mock XHR — no `readystatechange`, `load`, `error` or `abort` event ever
 * fires, so any `requestUploadWithProgress` promise hangs forever. MSW's
 * `fetch` interception, by contrast, works reliably (every other test relies
 * on it). This shim re-expresses the small slice of the XHR surface that
 * `api/client/http.ts::requestUploadWithProgress` uses on top of `fetch`, so
 * upload-based code becomes testable through the same MSW boundary.
 *
 * Production is untouched: the real browser XHR (with native upload progress)
 * runs unchanged; this class only replaces the global in the test runtime.
 */

type Listener = (ev: unknown) => void;

function dispatch(
  target: object,
  listeners: Record<string, Listener[]>,
  type: string,
  ev: unknown,
) {
  const prop = (target as Record<string, unknown>)['on' + type];
  if (typeof prop === 'function') (prop as Listener).call(target, ev);
  for (const l of listeners[type] ?? []) l.call(target, ev);
}

class FetchXHRUpload {
  onprogress: Listener | null = null;
  private _listeners: Record<string, Listener[]> = {};

  addEventListener(type: string, cb: Listener) {
    (this._listeners[type] ??= []).push(cb);
  }

  removeEventListener(type: string, cb: Listener) {
    this._listeners[type] = (this._listeners[type] ?? []).filter((l) => l !== cb);
  }

  emit(type: string, ev: unknown) {
    dispatch(this, this._listeners, type, ev);
  }
}

export class FetchXHR {
  static readonly UNSENT = 0;
  static readonly OPENED = 1;
  static readonly HEADERS_RECEIVED = 2;
  static readonly LOADING = 3;
  static readonly DONE = 4;

  readyState = 0;
  status = 0;
  statusText = '';
  responseText = '';
  response: unknown = null;
  responseType = '';
  withCredentials = false;
  timeout = 0;

  onload: Listener | null = null;
  onerror: Listener | null = null;
  onabort: Listener | null = null;
  onloadend: Listener | null = null;
  onreadystatechange: Listener | null = null;

  upload = new FetchXHRUpload();

  private _method = 'GET';
  private _url = '';
  private _headers: Record<string, string> = {};
  private _listeners: Record<string, Listener[]> = {};
  private _controller = new AbortController();
  private _aborted = false;

  open(method: string, url: string) {
    this._method = method;
    this._url = url;
    this.readyState = FetchXHR.OPENED;
  }

  setRequestHeader(key: string, value: string) {
    this._headers[key] = value;
  }

  addEventListener(type: string, cb: Listener) {
    (this._listeners[type] ??= []).push(cb);
  }

  removeEventListener(type: string, cb: Listener) {
    this._listeners[type] = (this._listeners[type] ?? []).filter((l) => l !== cb);
  }

  private _emit(type: string) {
    dispatch(this, this._listeners, type, { type, target: this });
  }

  send(body?: BodyInit | null) {
    fetch(this._url, {
      method: this._method,
      headers: this._headers,
      body: body ?? undefined,
      credentials: this.withCredentials ? 'include' : 'same-origin',
      signal: this._controller.signal,
    })
      .then(async (res) => {
        if (this._aborted) return;
        this.status = res.status;
        this.statusText = res.statusText;
        const text = await res.text();
        this.responseText = text;
        if (this.responseType === 'json') {
          try {
            this.response = text ? JSON.parse(text) : null;
          } catch {
            this.response = null;
          }
        } else {
          this.response = text;
        }
        this.readyState = FetchXHR.DONE;
        this.upload.emit('progress', { lengthComputable: true, loaded: 1, total: 1 });
        this._emit('readystatechange');
        this._emit('load');
        this._emit('loadend');
      })
      .catch(() => {
        if (this._aborted) return;
        this.readyState = FetchXHR.DONE;
        this._emit('error');
        this._emit('loadend');
      });
  }

  abort() {
    this._aborted = true;
    try {
      this._controller.abort();
    } catch {
      /* already aborted */
    }
    this._emit('abort');
    this._emit('loadend');
  }

  getAllResponseHeaders() {
    return '';
  }

  getResponseHeader() {
    return null;
  }
}

export function installFetchXHR() {
  globalThis.XMLHttpRequest = FetchXHR as unknown as typeof XMLHttpRequest;
}
