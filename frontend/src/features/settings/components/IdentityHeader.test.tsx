/**
 * Component test for IdentityHeader. It is presentational (no hooks beyond
 * local state, no i18n), so it renders with a bare ``render``. The avatar is
 * only shown for http/https URLs; everything else (and a load error) falls
 * back to coloured initials derived from the name, or the email when there is
 * no name.
 */

import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import IdentityHeader from './IdentityHeader';
import type { UserOut } from '../../../api/types/dto';

function user(overrides: Partial<UserOut> = {}): UserOut {
  return {
    user_id: 'u_1',
    email: 'ada@example.com',
    name: 'Ada Lovelace',
    avatar_url: null,
    ...overrides,
  };
}

describe('IdentityHeader', () => {
  it('renders the avatar image for an http(s) URL', () => {
    render(<IdentityHeader user={user({ avatar_url: 'https://cdn.example.com/a.png' })} />);
    const img = screen.getByRole('img', { name: 'Ada Lovelace' });
    expect(img).toHaveAttribute('src', 'https://cdn.example.com/a.png');
  });

  it('shows the two-letter name initials when there is no avatar', () => {
    render(<IdentityHeader user={user({ avatar_url: null })} />);
    expect(screen.queryByRole('img')).not.toBeInTheDocument();
    expect(screen.getByText('AL')).toBeInTheDocument();
  });

  it('derives initials from the email when there is no name', () => {
    // A single-word source (the email) yields its first two characters.
    render(<IdentityHeader user={user({ name: null, avatar_url: null })} />);
    expect(screen.getByText('AD')).toBeInTheDocument();
  });

  it('rejects a non-http(s) avatar protocol and shows initials instead', () => {
    render(<IdentityHeader user={user({ avatar_url: 'javascript:alert(1)' })} />);
    expect(screen.queryByRole('img')).not.toBeInTheDocument();
    expect(screen.getByText('AL')).toBeInTheDocument();
  });

  it('falls back to initials when the avatar image fails to load', () => {
    render(<IdentityHeader user={user({ avatar_url: 'https://cdn.example.com/broken.png' })} />);
    const img = screen.getByRole('img', { name: 'Ada Lovelace' });
    fireEvent.error(img);
    expect(screen.queryByRole('img')).not.toBeInTheDocument();
    expect(screen.getByText('AL')).toBeInTheDocument();
  });
});
