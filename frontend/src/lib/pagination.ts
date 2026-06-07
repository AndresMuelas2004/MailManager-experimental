// Pure pagination helpers shared by every paginated listing page.

// Parse the 1-based ``page`` search param, normalising any invalid value
// (missing, non-numeric, zero or negative) to page 1. ``parseInt('abc')``
// is ``NaN`` and ``NaN || 1`` is ``1``; ``'-3'`` clamps via ``Math.max``.
export function parsePageParam(searchParams: URLSearchParams): number {
  return Math.max(1, parseInt(searchParams.get('page') ?? '1', 10) || 1);
}
