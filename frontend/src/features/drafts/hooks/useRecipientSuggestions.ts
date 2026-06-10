import { useQuery } from '@tanstack/react-query';

import { getContactSuggestions } from '../../../api/endpoints/contacts';
import type { ContactSuggestion } from '../../../api/types/dto';

// Must stay aligned with the backend ``min_length=2`` and the lupa's
// ``MIN_SEARCH_LENGTH``; ``SUGGESTIONS_LIMIT`` mirrors the endpoint
// default. The canonical figures live in
// ``docs/limits/autocompletado-destinatarios.md``.
const MIN_QUERY_LENGTH = 2;
const SUGGESTIONS_LIMIT = 8;

export type UseRecipientSuggestionsReturn = {
  suggestions: ContactSuggestion[];
  loading: boolean;
};

// *query* must already be debounced by the caller (the host debounces it,
// mirroring how the lupa debounces ``?q=`` before passing it to useEmailList).
export default function useRecipientSuggestions(query: string): UseRecipientSuggestionsReturn {
  const trimmed = query.trim();
  const enabled = trimmed.length >= MIN_QUERY_LENGTH;

  const suggestionsQuery = useQuery({
    queryKey: ['contact-suggestions', enabled ? trimmed : null] as const,
    queryFn: ({ signal }) => getContactSuggestions(trimmed, { limit: SUGGESTIONS_LIMIT, signal }),
    enabled,
    // Deliberately NO ``placeholderData: keepPreviousData`` (unlike useEmailList):
    // when the user deletes back below 2 chars, ``enabled`` flips false and we
    // want ``suggestions`` to return to ``[]`` at once, not keep the stale list.
  });

  return {
    // Silent degradation: any error → empty list. The composer never breaks
    // because suggestions failed; the user keeps typing manually. No call to
    // ``toUiError`` and no exposed ``error`` — a failed query leaves ``data``
    // undefined, which collapses to ``[]``.
    suggestions: suggestionsQuery.data ?? [],
    loading: suggestionsQuery.isFetching && enabled,
  };
}
