import { request } from '../client/http';
import { backfillStatusListSchema, type BackfillStatusList } from '../types/dto';

// Accepts an optional ``signal`` like the other pollable GETs (getUnreadCount)
// so an in-flight poll is aborted when the query is cancelled/unmounted.
export function getBackfillStatus(
  mailboxId: string,
  signal?: AbortSignal,
): Promise<BackfillStatusList> {
  return request(`/mailboxes/${mailboxId}/backfill-status`, {
    schema: backfillStatusListSchema,
    signal,
  });
}
