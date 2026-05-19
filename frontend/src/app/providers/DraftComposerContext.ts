import { createContext, useContext } from 'react';

import type { DraftOut } from '../../api/types/dto';

// Public surface the rest of the app calls. Identical shape to the previous
// `features/drafts/hooks/DraftComposerContext` to keep call sites untouched.
export type DraftComposerImpl = {
  openForNewEmail: () => void;
  openForNewDraft: (args?: { accountId?: string }) => void;
  openForEditDraft: (draft: DraftOut) => void;
  setRefreshCallback: (fn: (() => void | Promise<void>) | null) => void;
};

// Why register pattern: `app/providers/` cannot import from `features/`
// (CLAUDE.md §8). The provider here only owns the slot; the feature mounts
// a host component that calls `useDraftComposer` and registers its impl
// through `__register`. Until the host registers, the four methods are
// no-ops — the sidebar's "Compose" button is harmless before the layout
// scope where the host lives is reached.
export type DraftComposerContextValue = DraftComposerImpl & {
  __register: (impl: DraftComposerImpl | null) => void;
};

const NOOP_IMPL: DraftComposerImpl = {
  openForNewEmail: () => {},
  openForNewDraft: () => {},
  openForEditDraft: () => {},
  setRefreshCallback: () => {},
};

export const DraftComposerContext = createContext<DraftComposerContextValue>({
  ...NOOP_IMPL,
  __register: () => {},
});

export function useDraftComposerContext(): DraftComposerContextValue {
  return useContext(DraftComposerContext);
}
