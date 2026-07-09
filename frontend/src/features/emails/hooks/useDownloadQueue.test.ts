/**
 * Unit tests for ``useDownloadQueue``.
 *
 * The hook owns a FIFO queue with a hard concurrency cap of 2 (D-24).
 * Tests assert:
 *  - happy path enqueue + resolve order with concurrency respected,
 *  - cancellation of both active and queued downloads,
 *  - status() reflects the lifecycle (queued → downloading → idle/error),
 *  - errors short-circuit to ``error`` status until a successful retry.
 */

import { act, renderHook, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import useDownloadQueue, { type DownloadFn } from './useDownloadQueue';

type Resolver = {
  resolve: (value: { blob: Blob; filename: string }) => void;
  reject: (reason: unknown) => void;
  fn: DownloadFn;
  started: () => boolean;
};

function makeDeferred(): Resolver {
  // The hook calls ``fn()`` the moment the slot becomes free, so we
  // capture ``res``/``rej`` inside the executor and expose them via
  // ``resolve``/``reject`` regardless of when the caller decides to
  // settle the download. ``started`` lets tests assert that the
  // download actually entered the active set before any settlement.
  let resolveCb: Resolver['resolve'] = () => undefined;
  let rejectCb: Resolver['reject'] = () => undefined;
  let didStart = false;
  const fn: DownloadFn = () =>
    new Promise<{ blob: Blob; filename: string }>((res, rej) => {
      didStart = true;
      resolveCb = res;
      rejectCb = rej;
    });
  return {
    fn,
    started: () => didStart,
    resolve: (value) => resolveCb(value),
    reject: (reason) => rejectCb(reason),
  };
}

function blobFor(text: string) {
  return new Blob([text], { type: 'application/octet-stream' });
}

describe('useDownloadQueue — concurrency', () => {
  it('starts the first two downloads immediately and queues the rest', async () => {
    const a = makeDeferred();
    const b = makeDeferred();
    const c = makeDeferred();

    const { result } = renderHook(() => useDownloadQueue());

    let promiseA: Promise<unknown>;
    let promiseB: Promise<unknown>;
    let promiseC: Promise<unknown>;

    act(() => {
      promiseA = result.current.enqueue('a', a.fn);
      promiseB = result.current.enqueue('b', b.fn);
      promiseC = result.current.enqueue('c', c.fn);
    });

    // 'a' and 'b' are downloading, 'c' is queued (max concurrency = 2).
    await waitFor(() => {
      expect(result.current.status('a')).toBe('downloading');
      expect(result.current.status('b')).toBe('downloading');
      expect(result.current.status('c')).toBe('queued');
    });

    // Resolve 'a' — 'c' should drain into the active slot.
    act(() => {
      a.resolve({ blob: blobFor('A'), filename: 'a.pdf' });
    });

    await expect(promiseA!).resolves.toEqual({ blob: expect.any(Blob), filename: 'a.pdf' });

    await waitFor(() => {
      expect(result.current.status('a')).toBe('idle');
      expect(result.current.status('c')).toBe('downloading');
    });

    // Resolve the rest so the test does not leak unsettled promises.
    act(() => {
      b.resolve({ blob: blobFor('B'), filename: 'b.pdf' });
      c.resolve({ blob: blobFor('C'), filename: 'c.pdf' });
    });
    await expect(promiseB!).resolves.toBeDefined();
    await expect(promiseC!).resolves.toBeDefined();
  });
});

describe('useDownloadQueue — cancel', () => {
  it('cancel of a queued download rejects its promise and removes it from the queue', async () => {
    const a = makeDeferred();
    const b = makeDeferred();
    const c = makeDeferred();

    const { result } = renderHook(() => useDownloadQueue());

    let promiseA: Promise<unknown>;
    let promiseB: Promise<unknown>;
    let promiseC: Promise<unknown>;
    act(() => {
      promiseA = result.current.enqueue('a', a.fn);
      promiseB = result.current.enqueue('b', b.fn);
      promiseC = result.current.enqueue('c', c.fn);
    });

    await waitFor(() => {
      expect(result.current.status('c')).toBe('queued');
    });

    act(() => {
      result.current.cancel('c');
    });

    await expect(promiseC!).rejects.toThrow('Download cancelled.');

    await waitFor(() => {
      expect(result.current.status('c')).toBe('idle');
    });

    // Cleanup
    act(() => {
      a.resolve({ blob: blobFor('A'), filename: 'a.pdf' });
      b.resolve({ blob: blobFor('B'), filename: 'b.pdf' });
    });
    await expect(promiseA!).resolves.toBeDefined();
    await expect(promiseB!).resolves.toBeDefined();
  });

  it('cancel of an active download aborts it and frees the slot for the next queued', async () => {
    const a = makeDeferred();
    const b = makeDeferred();
    const c = makeDeferred();

    const { result } = renderHook(() => useDownloadQueue());

    act(() => {
      void result.current.enqueue('a', a.fn);
      void result.current.enqueue('b', b.fn);
      void result.current.enqueue('c', c.fn);
    });

    await waitFor(() => {
      expect(result.current.status('a')).toBe('downloading');
      expect(result.current.status('c')).toBe('queued');
    });

    act(() => {
      result.current.cancel('a');
    });

    await waitFor(() => {
      // 'a' is no longer active; 'c' has been promoted into the slot.
      expect(result.current.status('a')).toBe('idle');
      expect(result.current.status('c')).toBe('downloading');
    });

    act(() => {
      b.resolve({ blob: blobFor('B'), filename: 'b.pdf' });
      c.resolve({ blob: blobFor('C'), filename: 'c.pdf' });
    });
  });
});

describe('useDownloadQueue — error', () => {
  it('rejected download surfaces error status until a successful re-enqueue clears it', async () => {
    const failing: DownloadFn = vi.fn(() => Promise.reject(new Error('boom')));
    const { result } = renderHook(() => useDownloadQueue());

    let p: Promise<unknown>;
    act(() => {
      p = result.current.enqueue('a', failing);
    });
    await expect(p!).rejects.toThrow('boom');

    await waitFor(() => {
      expect(result.current.status('a')).toBe('error');
    });
    expect(result.current.errorOf('a')).toBeDefined();

    // Successful retry clears the error.
    const ok: DownloadFn = () => Promise.resolve({ blob: blobFor('OK'), filename: 'ok.pdf' });
    act(() => {
      void result.current.enqueue('a', ok);
    });
    await waitFor(() => {
      expect(result.current.status('a')).toBe('idle');
      expect(result.current.errorOf('a')).toBeUndefined();
    });
  });
});
