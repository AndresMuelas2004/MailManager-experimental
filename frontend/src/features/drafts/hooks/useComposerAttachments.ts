import { useCallback, useEffect, useRef, useState } from 'react';

import { addDraftAttachment, removeDraftAttachment } from '../../../api/endpoints/attachments';
import {
  humaniseAttachmentError,
  validateFileForUpload,
  type ValidationResult,
} from '../../../lib/attachments';
import { toUiError, type UiError } from '../../../api/client/errors';
import type { DraftAttachmentMetadata } from '../../../api/types/dto';

export type ChipStatus = 'uploading' | 'uploaded' | 'failed';

export type AttachmentChip = {
  id: string; // 'tmp-XYZ' while uploading, then real uuid
  filename: string;
  mimeType: string;
  size: number;
  position: number;
  status: ChipStatus;
  progress?: number;
  error?: UiError;
  abortController?: AbortController;
  providerAttachmentId: string | null;
};

export type AttachmentTarget = {
  mailboxId: string;
  accountId: string;
  providerDraftId: string;
};

export type UseComposerAttachmentsReturn = {
  chips: AttachmentChip[];
  totalSize: number;
  count: number;
  isUploading: boolean;
  hasFailedChips: boolean;
  reset: () => void;
  seedFromDraft: (initial: DraftAttachmentMetadata[]) => void;
  addFiles: (files: File[], resolveTarget: () => Promise<AttachmentTarget | null>) => void;
  removeChip: (chipId: string, target: AttachmentTarget) => Promise<void>;
  isDirty: () => boolean;
};

let nanoidCounter = 0;
function tmpId(): string {
  nanoidCounter += 1;
  return `tmp-${Date.now().toString(36)}-${nanoidCounter}`;
}

/**
 * Composer attachments orchestrator (D-25).
 *
 * Owns the chip state, runs client-side validation (D-01..D-04a),
 * uploads via ``addDraftAttachment`` with real upload progress, and
 * exposes a flat API the composer renders.
 */
export default function useComposerAttachments(): UseComposerAttachmentsReturn {
  const [chips, setChips] = useState<AttachmentChip[]>([]);
  const chipsRef = useRef<AttachmentChip[]>([]);
  useEffect(() => {
    chipsRef.current = chips;
  }, [chips]);
  // Set of attachment ids that were already saved when the composer was seeded
  // from an existing draft (edit_draft / inherited forward). They form the
  // "clean" baseline: reopening a draft with its saved attachments untouched
  // must NOT read as dirty (otherwise closing pops the unsaved-changes dialog
  // for a draft nobody edited). Only a real add or remove diverges from it.
  const baselineIdsRef = useRef<Set<string>>(new Set());

  const totalSize = chips.reduce((sum, chip) => sum + chip.size, 0);
  const count = chips.length;
  const isUploading = chips.some((chip) => chip.status === 'uploading');
  const hasFailedChips = chips.some((chip) => chip.status === 'failed');

  const updateChip = useCallback((id: string, patch: Partial<AttachmentChip>) => {
    setChips((prev) => prev.map((c) => (c.id === id ? { ...c, ...patch } : c)));
  }, []);

  const removeChipLocal = useCallback((id: string) => {
    setChips((prev) => prev.filter((c) => c.id !== id));
  }, []);

  const reset = useCallback(() => {
    chipsRef.current.forEach((chip) => {
      if (chip.status === 'uploading' && chip.abortController) {
        chip.abortController.abort();
      }
    });
    setChips([]);
    baselineIdsRef.current = new Set();
  }, []);

  const seedFromDraft = useCallback((initial: DraftAttachmentMetadata[]) => {
    baselineIdsRef.current = new Set(initial.map((meta) => meta.draft_attachment_id));
    setChips(
      initial.map((meta) => ({
        id: meta.draft_attachment_id,
        filename: meta.filename,
        mimeType: meta.mime_type,
        size: meta.size,
        position: meta.position,
        status: 'uploaded',
        providerAttachmentId: meta.provider_attachment_id,
      })),
    );
  }, []);

  const addFiles = useCallback(
    (files: File[], resolveTarget: () => Promise<AttachmentTarget | null>) => {
      let runningTotal = chipsRef.current.reduce((sum, c) => sum + c.size, 0);
      let runningCount = chipsRef.current.length;
      const accepted: File[] = [];

      for (const file of files) {
        const validation: ValidationResult = validateFileForUpload(
          file,
          runningTotal,
          runningCount,
        );
        if (!validation.ok) {
          const failedId = tmpId();
          setChips((prev) => [
            ...prev,
            {
              id: failedId,
              filename: file.name,
              mimeType: file.type || 'application/octet-stream',
              size: file.size,
              position: prev.length,
              status: 'failed',
              error: { message: validation.message, code: validation.reason },
              providerAttachmentId: null,
            },
          ]);
          // Auto-clear after 3s so the toast-like chip does not linger.
          setTimeout(() => removeChipLocal(failedId), 3000);
          continue;
        }
        runningTotal += file.size;
        runningCount += 1;
        accepted.push(file);
      }

      // Client-side validation (above) is the first line of defence (§4.2).
      // The provider-draft bootstrap rides on ``resolveTarget``, so it is
      // invoked ONLY when at least one file passed validation — a drop that
      // is fully rejected (e.g. a blocked .exe) must not create a phantom
      // draft nor lock the account selector.
      if (accepted.length === 0) return;

      resolveTarget()
        .then((target) => {
          if (!target) return;
          accepted.forEach((file) => {
            const chipId = tmpId();
            const controller = new AbortController();
            setChips((prev) => [
              ...prev,
              {
                id: chipId,
                filename: file.name,
                mimeType: file.type || 'application/octet-stream',
                size: file.size,
                position: prev.length,
                status: 'uploading',
                progress: 0,
                abortController: controller,
                providerAttachmentId: null,
              },
            ]);
            addDraftAttachment(target.mailboxId, target.accountId, target.providerDraftId, file, {
              onProgress: (pct) => updateChip(chipId, { progress: pct }),
              signal: controller.signal,
            })
              .then((response) => {
                updateChip(chipId, {
                  id: response.draft_attachment_id,
                  filename: response.filename,
                  mimeType: response.mime_type,
                  size: response.size,
                  position: response.position,
                  status: 'uploaded',
                  progress: undefined,
                  abortController: undefined,
                  providerAttachmentId: response.provider_attachment_id,
                });
              })
              .catch((error) => {
                const ui = toUiError(error);
                const message = humaniseAttachmentError(ui.code, undefined) ?? ui.message;
                updateChip(chipId, {
                  status: 'failed',
                  error: { message, code: ui.code },
                  progress: undefined,
                  abortController: undefined,
                });
                setTimeout(() => removeChipLocal(chipId), 3000);
              });
          });
        })
        .catch(() => {
          // Bootstrap failed (createDraft rejected). ``ensureProviderDraftId``
          // already routed the error to ``persistence.error``; no upload chips
          // were created for the accepted files, so nothing to roll back.
        });
    },
    [removeChipLocal, updateChip],
  );

  const removeChip = useCallback(
    async (chipId: string, target: AttachmentTarget) => {
      const chip = chipsRef.current.find((c) => c.id === chipId);
      if (!chip) return;
      if (chip.status === 'uploading') {
        chip.abortController?.abort();
        removeChipLocal(chipId);
        return;
      }
      // Optimistic UI: remove first, restore on failure.
      removeChipLocal(chipId);
      try {
        await removeDraftAttachment(
          target.mailboxId,
          target.accountId,
          target.providerDraftId,
          chip.id,
        );
      } catch (error) {
        const ui = toUiError(error);
        // Restore the chip with an error annotation so the user knows
        // the deletion did not stick.
        setChips((prev) => [
          ...prev,
          {
            ...chip,
            status: 'failed',
            error: { message: ui.message, code: ui.code },
          },
        ]);
        setTimeout(() => removeChipLocal(chip.id), 3000);
      }
    },
    [removeChipLocal],
  );

  // Dirty iff the live attachment set diverges from the seeded baseline.
  // Transient ``failed`` chips (rejected validation / failed upload, both
  // auto-cleared after 3s and never persisted) are excluded so they do not
  // spuriously flag a clean composer as modified.
  const isDirty = useCallback((): boolean => {
    const baseline = baselineIdsRef.current;
    const liveIds = chips.filter((c) => c.status !== 'failed').map((c) => c.id);
    if (liveIds.length !== baseline.size) return true;
    return liveIds.some((id) => !baseline.has(id));
  }, [chips]);

  return {
    chips,
    totalSize,
    count,
    isUploading,
    hasFailedChips,
    reset,
    seedFromDraft,
    addFiles,
    removeChip,
    isDirty,
  };
}
