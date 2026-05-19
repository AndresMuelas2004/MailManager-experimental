import { useCallback, useMemo, useState, type ReactNode } from 'react';

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
export default function DraftComposerProvider({ children }: { children: ReactNode }) {
  const [impl, setImpl] = useState<DraftComposerImpl | null>(null);

  const register = useCallback((next: DraftComposerImpl | null) => {
    setImpl(next);
  }, []);

  const value = useMemo<DraftComposerContextValue>(() => {
    if (impl) {
      return { ...impl, __register: register };
    }
    return {
      openForNewEmail: () => {},
      openForNewDraft: () => {},
      openForEditDraft: () => {},
      setRefreshCallback: () => {},
      __register: register,
    };
  }, [impl, register]);

  return <DraftComposerContext.Provider value={value}>{children}</DraftComposerContext.Provider>;
}
