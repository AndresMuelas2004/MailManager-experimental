type Props = {
  ariaLabel: string;
  message: string;
  acceptLabel: string;
  rejectLabel: string;
  policyHref: string;
  policyLabel: string;
  onAccept: () => void;
  onReject: () => void;
};

/**
 * Bottom consent bar. Purely presentational: every string and the policy URL
 * arrive as props, and the choice is reported through the two callbacks — the
 * caller owns persistence and whether any tag gets loaded.
 */
export default function CookieBanner({
  ariaLabel,
  message,
  acceptLabel,
  rejectLabel,
  policyHref,
  policyLabel,
  onAccept,
  onReject,
}: Props) {
  return (
    <div
      role="region"
      aria-label={ariaLabel}
      className="fixed inset-x-0 bottom-0 z-50 border-t border-zinc-200 bg-white/95 p-4 shadow-2xl backdrop-blur"
    >
      <div className="mx-auto flex max-w-4xl flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <p className="text-sm leading-relaxed text-zinc-600">
          {message}{' '}
          <a href={policyHref} className="font-medium text-zinc-900 underline hover:text-zinc-700">
            {policyLabel}
          </a>
        </p>
        <div className="flex shrink-0 gap-2">
          <button
            type="button"
            onClick={onReject}
            className="rounded-lg border border-zinc-200 px-4 py-2 text-sm font-medium text-zinc-700 hover:bg-zinc-50"
          >
            {rejectLabel}
          </button>
          <button
            type="button"
            onClick={onAccept}
            className="rounded-lg bg-zinc-900 px-4 py-2 text-sm font-medium text-white hover:bg-zinc-700"
          >
            {acceptLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
