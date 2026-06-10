import { request } from '../client/http';
import { contactSuggestionsResponseSchema, type ContactSuggestionsResponse } from '../types/dto';

export type GetContactSuggestionsOptions = {
  limit?: number;
  signal?: AbortSignal;
};

export function getContactSuggestions(
  q: string,
  options: GetContactSuggestionsOptions = {},
): Promise<ContactSuggestionsResponse> {
  const params = new URLSearchParams({ q });
  if (options.limit !== undefined) params.set('limit', String(options.limit));
  return request(`/contacts/suggestions?${params}`, {
    schema: contactSuggestionsResponseSchema,
    signal: options.signal,
  });
}
