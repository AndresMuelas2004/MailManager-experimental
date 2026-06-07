// Shared, domain-agnostic constants reusable across layers.

// Fixed page size for every email listing (real, unified, account,
// favourites, virtual and search). Lives in ``lib/`` so both the API
// endpoint layer (which builds ``limit``/``offset``) and the feature
// layer (hooks + the pagination component) can import it without
// crossing the ``api/ → features/`` boundary.
export const EMAILS_PAGE_SIZE = 50;
