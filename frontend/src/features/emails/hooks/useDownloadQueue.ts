import { useCallback, useRef, useState } from 'react';

import { toUiError, type UiError } from '../../../api/client/errors';

/**
 * Concurrent attachment download cap (D-24).
 *
 * Backend Outlook calls share a 4-concurrency budget per (appId,
 * mailbox); the user firing six download clicks in a row would
 * trip throttle and the retries pile up. Two simultaneous keeps
 * the perceived UX snappy without straining the upstream cap.
 */
const MAX_CONCURRENT_DOWNLOADS = 2;

export type DownloadFn = () => Promise<{ blob: Blob; filename: string }>;

export type DownloadStatus = 'idle' | 'queued' | 'downloading' | 'error';

type ActiveEntry = {
  id: string;
  controller: AbortController;
};

type QueuedEntry = {
  id: string;
  fn: DownloadFn;
  resolve: (value: { blob: Blob; filename: string }) => void;
  reject: (reason: unknown) => void;
};

export type UseDownloadQueueReturn = {
  enqueue: (id: string, fn: DownloadFn) => Promise<{ blob: Blob; filename: string }>;
  cancel: (id: string) => void;
  status: (id: string) => DownloadStatus;
  errorOf: (id: string) => UiError | undefined;
};

/**
 * Hook-managed FIFO queue of attachment downloads with bounded
 * concurrency. The component renders state via ``status(id)``; clicking
 * a tile while a download is active short-circuits to ``cancel(id)``.
 */
export default function useDownloadQueue(): UseDownloadQueueReturn {
  const activeRef = useRef<Map<string, ActiveEntry>>(new Map());
  const queueRef = useRef<QueuedEntry[]>([]);
  const [active, setActive] = useState<readonly string[]>([]);
  const [queued, setQueued] = useState<readonly string[]>([]);
  const [errors, setErrors] = useState<Record<string, UiError>>({});

  const refreshSnapshots = useCallback(() => {
    setActive(Array.from(activeRef.current.keys()));
    setQueued(queueRef.current.map((entry) => entry.id));
  }, []);

  const drainQueue = useCallback(() => {
    while (
      activeRef.current.size < MAX_CONCURRENT_DOWNLOADS
      && queueRef.current.length > 0
    ) {
      const next = queueRef.current.shift()!;
      const controller = new AbortController();
      activeRef.current.set(next.id, { id: next.id, controller });
      refreshSnapshots();

      next
        .fn()
        .then((result) => {
          activeRef.current.delete(next.id);
          setErrors((prev) => {
            if (!(next.id in prev)) return prev;
            const copy = { ...prev };
            delete copy[next.id];
            return copy;
          });
          next.resolve(result);
          refreshSnapshots();
          drainQueue();
        })
        .catch((error) => {
          activeRef.current.delete(next.id);
          const ui = toUiError(error);
          setErrors((prev) => ({ ...prev, [next.id]: ui }));
          next.reject(error);
          refreshSnapshots();
          drainQueue();
        });
    }
  }, [refreshSnapshots]);

  const enqueue = useCallback(
    (id: string, fn: DownloadFn): Promise<{ blob: Blob; filename: string }> => {
      return new Promise((resolve, reject) => {
        queueRef.current.push({ id, fn, resolve, reject });
        refreshSnapshots();
        drainQueue();
      });
    },
    [drainQueue, refreshSnapshots],
  );

  const cancel = useCallback((id: string) => {
    const live = activeRef.current.get(id);
    if (live) {
      live.controller.abort();
      activeRef.current.delete(id);
      refreshSnapshots();
      drainQueue();
      return;
    }
    const idx = queueRef.current.findIndex((entry) => entry.id === id);
    if (idx >= 0) {
      const removed = queueRef.current.splice(idx, 1)[0];
      removed.reject(new Error('Download cancelled.'));
      refreshSnapshots();
    }
  }, [drainQueue, refreshSnapshots]);

  const status = useCallback(
    (id: string): DownloadStatus => {
      if (errors[id]) return 'error';
      if (active.includes(id)) return 'downloading';
      if (queued.includes(id)) return 'queued';
      return 'idle';
    },
    [active, queued, errors],
  );

  const errorOf = useCallback((id: string) => errors[id], [errors]);

  return { enqueue, cancel, status, errorOf };
}
