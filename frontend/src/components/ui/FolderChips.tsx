import type { FolderRef } from '../../api/types/dto';

type Props = {
  folders: FolderRef[];
  className?: string;
};

// Presentational: the small colour-coded tags showing which folders an email
// belongs to. Domain-aware (knows about ``FolderRef``) but does no fetching —
// data arrives by props (components/ui §2.2). Renders nothing when empty so
// every EmailTable / viewer mount can drop it in unconditionally.
export default function FolderChips({ folders, className }: Props) {
  if (folders.length === 0) return null;
  return (
    <span className={`flex flex-wrap items-center gap-1 ${className ?? ''}`}>
      {folders.map((folder) => (
        <span
          key={folder.folder_id}
          className="inline-flex max-w-[140px] items-center gap-1 rounded-full bg-zinc-100 px-2 py-0.5 text-[11px] font-medium text-zinc-600"
        >
          <span
            className="h-2 w-2 shrink-0 rounded-full"
            // The folder colour is genuinely dynamic (user data) — an inline
            // style is the correct escape hatch. Falls back to a neutral dot
            // when the folder has no colour.
            style={{ backgroundColor: folder.color ?? '#a1a1aa' }}
            aria-hidden
          />
          <span className="truncate">{folder.name}</span>
        </span>
      ))}
    </span>
  );
}
