import '@testing-library/jest-dom/vitest';
import { cleanup } from '@testing-library/react';
import { afterAll, afterEach, beforeAll } from 'vitest';

// Strip RequestInit.signal at the Request constructor level. jsdom 25 + MSW
// v2 validate `init.signal instanceof AbortSignal` against the AbortSignal
// class jsdom captured at module load time. TanStack Query (and any code
// that pulls AbortController from a separately bundled realm) emits signals
// whose prototype chain ends in a sibling AbortSignal, so the cross-realm
// `instanceof` check throws "Expected signal to be an instance of
// AbortSignal" inside `new Request(...)` — the path MSW uses to build its
// intercepted request. Production runs in a single-realm browser and is
// unaffected; tests don't rely on aborting in-flight requests, so dropping
// the signal at construction time is safe and isolated to the test runtime.
{
  const OriginalRequest = globalThis.Request;
  globalThis.Request = new Proxy(OriginalRequest, {
    construct(target, args, newTarget) {
      const init = args[1];
      if (init && typeof init === 'object' && 'signal' in init) {
        const rest: RequestInit = { ...(init as RequestInit) };
        delete rest.signal;
        return Reflect.construct(target, [args[0], rest], newTarget);
      }
      return Reflect.construct(target, args, newTarget);
    },
  });
}

import { server } from './msw/server';
import { installFetchXHR } from './xhr-fetch-shim';

// Start the mock service worker before any test runs. `onUnhandledRequest:
// 'error'` turns unexpected network traffic into a loud failure instead of
// letting the request escape into the real network — a broken boundary is
// always a test bug, never a silent success.
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));

// MSW v2's XHR interceptor intercepts the request but never delivers the
// response in this jsdom runtime (no terminal event fires, so upload promises
// hang). Replace the global XMLHttpRequest with a fetch-delegating shim AFTER
// `server.listen()` has applied its own XHR patch, so XHR-based upload code
// routes through MSW's reliable fetch interception. See `xhr-fetch-shim.ts`.
beforeAll(() => installFetchXHR());

// Reset any per-test `server.use(...)` overrides so one test's failure
// simulation never leaks into the next one. Also unmount any component
// left over from the previous render — with `globals: false`, Testing
// Library's auto-cleanup is not wired, so we do it here.
afterEach(() => {
  cleanup();
  server.resetHandlers();
});

// Close the server cleanly after the suite completes.
afterAll(() => server.close());
