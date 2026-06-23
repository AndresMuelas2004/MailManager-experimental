/**
 * Unit tests for the Badge primitive.
 *
 * Badge is a pure, domain-agnostic component (no providers, no HTTP): the
 * already-translated ``aria-label`` arrives by prop from the parent, so it
 * renders standalone. These tests pin its only logic — hide at 0/negative,
 * render the count, cap the visible value at "99+" — and that the
 * accessible label is exposed.
 */

import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import Badge from './Badge';

describe('Badge', () => {
  it('renders nothing when the count is zero', () => {
    const { container } = render(<Badge count={0} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('renders nothing for a negative count', () => {
    const { container } = render(<Badge count={-3} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('renders the exact count for a positive value', () => {
    render(<Badge count={7} />);
    expect(screen.getByText('7')).toBeInTheDocument();
  });

  it('renders the boundary value 99 verbatim (no cap)', () => {
    render(<Badge count={99} />);
    expect(screen.getByText('99')).toBeInTheDocument();
  });

  it('caps the visible value at "99+" above 99', () => {
    render(<Badge count={150} />);
    expect(screen.getByText('99+')).toBeInTheDocument();
    expect(screen.queryByText('150')).not.toBeInTheDocument();
  });

  it('exposes the provided aria-label', () => {
    render(<Badge count={5} aria-label="5 sin leer" />);
    expect(screen.getByLabelText('5 sin leer')).toHaveTextContent('5');
  });
});
