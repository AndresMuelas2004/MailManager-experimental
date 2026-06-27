import { useCallback, useMemo, useRef, type ReactNode } from 'react';

import {
  DraftComposerContext,
  type DraftComposerContextValue,
  type DraftComposerImpl,
} from './DraftComposerContext';

// Headless provider: holds the registered impl (or null) and exposes the
// context. The actual `useDraftComposer` hook + the UI overlays live in the
// drafts feature, mounted by `DraftComposerHost` (registered via __register).
// Keeping this file free of `features/` and `components/ui/` imports satisfies
// the app/providers boundary in CLAUDE.md §8.
//
// The impl is kept in a ref (not useState) so that `register` does not cause a
// re-render. The host re-registers on every render because its callbacks
// are derived from non-memoised composer sub-hooks; setState here would
// re-render the host, which would re-register, producing an infinite loop.
export default function DraftComposerProvider({ children }: { children: ReactNode }) {
  const implRef = useRef<DraftComposerImpl | null>(null);
  // Buffers the latest refresh callback registered by the active listing page.
  // The content <Outlet/> commits before its sibling DraftComposerHost (single
  // Suspense boundary in RootLayout → tree-order effects), so a page runs
  // setRefreshCallback while implRef is still null and the callback would be
  // silently dropped — leaving the composer unable to refetch the list, so a
  // freshly-saved draft only shows up after a remount. Replaying the buffer
  // when the host registers restores the wiring regardless of mount order.
  const refreshCallbackRef = useRef<(() => void | Promise<void>) | null>(null);

  const register = useCallback((next: DraftComposerImpl | null) => {
    implRef.current = next;
    if (next) next.setRefreshCallback(refreshCallbackRef.current);
  }, []);

  const value = useMemo<DraftComposerContextValue>(
    () => ({
      openForNewEmail: () => implRef.current?.openForNewEmail(),
      openForNewDraft: (args) => implRef.current?.openForNewDraft(args),
      openForEditDraft: (draft) => implRef.current?.openForEditDraft(draft),
      openForReply: async (email) => {
        // Resolve to a no-op promise when the host has not registered
        // (sidebar buttons clicked before MailboxLayoutPage mounts).
        if (!implRef.current) return;
        await implRef.current.openForReply(email);
      },
      openForReplyAll: async (email) => {
        if (!implRef.current) return;
        await implRef.current.openForReplyAll(email);
      },
      openForForward: async (email) => {
        if (!implRef.current) return;
        await implRef.current.openForForward(email);
      },
      setRefreshCallback: (fn) => {
        refreshCallbackRef.current = fn;
        implRef.current?.setRefreshCallback(fn);
      },
      __register: register,
    }),
    [register],
  );

  return <DraftComposerContext.Provider value={value}>{children}</DraftComposerContext.Provider>;
}
