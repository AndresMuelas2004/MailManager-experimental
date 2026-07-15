import type { ReactNode } from 'react';

type Props = {
  href: string;
  children: ReactNode;
};

/**
 * Anchor for the external references inside the legal documents. Every
 * outbound link must open in a new tab with the opener severed.
 */
export default function ExternalLink({ href, children }: Props) {
  return (
    <a href={href} target="_blank" rel="noopener noreferrer">
      {children}
    </a>
  );
}
