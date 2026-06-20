import { useState } from 'react';

import type { UserOut } from '../../../api/types/dto';

type Props = {
  user: UserOut;
};

// Only http/https avatars are rendered into ``<img src>`` (components/CLAUDE.md
// §3.7: never feed a ``src`` that could resolve to ``javascript:`` /
// ``data:text/html``). Any other protocol, or a missing URL, falls back to the
// coloured initials badge.
function isSafeAvatarUrl(url: string | null): url is string {
  if (!url) return false;
  try {
    const protocol = new URL(url).protocol.toLowerCase();
    return protocol === 'http:' || protocol === 'https:';
  } catch {
    return false;
  }
}

// Initials from the name (first letters of up to two words); falls back to the
// first character of the email. Always upper-cased; never empty for a present
// email.
function initialsFrom(name: string | null, email: string): string {
  const source = name && name.trim().length > 0 ? name.trim() : email;
  const words = source.split(/\s+/).filter(Boolean);
  if (words.length === 0) return email.slice(0, 1).toUpperCase();
  if (words.length === 1) return words[0].slice(0, 2).toUpperCase();
  return (words[0][0] + words[1][0]).toUpperCase();
}

export default function IdentityHeader({ user }: Props) {
  const [imageFailed, setImageFailed] = useState(false);
  const showImage = !imageFailed && isSafeAvatarUrl(user.avatar_url);
  const displayName = user.name ?? user.email;

  return (
    <div className="flex items-center gap-4">
      {showImage ? (
        <img
          src={user.avatar_url as string}
          alt={displayName}
          referrerPolicy="no-referrer"
          onError={() => setImageFailed(true)}
          className="h-14 w-14 shrink-0 rounded-full object-cover"
        />
      ) : (
        <div className="grid h-14 w-14 shrink-0 place-items-center rounded-full bg-blue-100 text-lg font-semibold text-blue-700">
          {initialsFrom(user.name, user.email)}
        </div>
      )}
      <div className="flex min-w-0 flex-col">
        <span className="truncate text-base font-semibold text-zinc-900">{displayName}</span>
        <span className="truncate text-sm text-zinc-500">{user.email}</span>
      </div>
    </div>
  );
}
