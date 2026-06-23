type Props = {
  count: number;
  className?: string;
  'aria-label'?: string;
};

// Numeric pill for unread counts. Renders nothing when count <= 0 (no "0"
// badge). Caps the visible value at "99+".
export default function Badge({ count, className, 'aria-label': ariaLabel }: Props) {
  if (count <= 0) return null;
  const label = count > 99 ? '99+' : String(count);
  return (
    <span
      className={`inline-flex min-w-[18px] items-center justify-center rounded-full bg-blue-600 px-1.5 text-[11px] font-semibold leading-[18px] text-white ${className ?? ''}`}
      aria-label={ariaLabel}
    >
      {label}
    </span>
  );
}
