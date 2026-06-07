import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import EmailPagination from './EmailPagination';

// EmailPagination is purely presentational: it renders the navigation
// controls (prev / elided page numbers / next) and derives their
// enabled/disabled state entirely from its props. The "from–to de total"
// range moved to EmailTable's header and is covered by EmailTable.test.tsx.
// No MSW, no router — a plain render is enough.

describe('EmailPagination', () => {
  it('renders nothing when there are no emails', () => {
    const { container } = render(
      <EmailPagination page={1} pageSize={50} total={0} onPageChange={() => {}} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it('disables Anterior on the first page and enables Siguiente', () => {
    render(<EmailPagination page={1} pageSize={50} total={500} onPageChange={() => {}} />);
    expect(screen.getByRole('button', { name: 'Página anterior' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Página siguiente' })).toBeEnabled();
  });

  it('disables Siguiente on the last page', () => {
    render(<EmailPagination page={10} pageSize={50} total={500} onPageChange={() => {}} />);
    expect(screen.getByRole('button', { name: 'Página siguiente' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Página anterior' })).toBeEnabled();
  });

  it('disables both controls when there is a single page', () => {
    render(<EmailPagination page={1} pageSize={50} total={37} onPageChange={() => {}} />);
    expect(screen.getByRole('button', { name: 'Página anterior' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Página siguiente' })).toBeDisabled();
  });

  it('marks the current page button with aria-current and disables it', () => {
    render(<EmailPagination page={3} pageSize={50} total={500} onPageChange={() => {}} />);
    const current = screen.getByRole('button', { name: 'Página 3' });
    expect(current).toHaveAttribute('aria-current', 'page');
    expect(current).toBeDisabled();
  });

  it('calls onPageChange with the next page when Siguiente is clicked', async () => {
    const onPageChange = vi.fn();
    render(<EmailPagination page={2} pageSize={50} total={500} onPageChange={onPageChange} />);
    await userEvent.click(screen.getByRole('button', { name: 'Página siguiente' }));
    expect(onPageChange).toHaveBeenCalledWith(3);
  });

  it('calls onPageChange with the previous page when Anterior is clicked', async () => {
    const onPageChange = vi.fn();
    render(<EmailPagination page={2} pageSize={50} total={500} onPageChange={onPageChange} />);
    await userEvent.click(screen.getByRole('button', { name: 'Página anterior' }));
    expect(onPageChange).toHaveBeenCalledWith(1);
  });

  it('calls onPageChange with the clicked page number', async () => {
    const onPageChange = vi.fn();
    // total 150 / 50 → 3 pages, so every number is visible (no elision).
    render(<EmailPagination page={1} pageSize={50} total={150} onPageChange={onPageChange} />);
    await userEvent.click(screen.getByRole('button', { name: 'Página 3' }));
    expect(onPageChange).toHaveBeenCalledWith(3);
  });

  it('elides the middle of a long page list with an ellipsis', () => {
    // 2000 total / 50 → 40 pages. On page 20 the window keeps 1, 18–22, 40
    // and collapses both gaps with "…".
    render(<EmailPagination page={20} pageSize={50} total={2000} onPageChange={() => {}} />);
    expect(screen.getByRole('button', { name: 'Página 1' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Página 40' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Página 18' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Página 22' })).toBeInTheDocument();
    // A far page like 10 is collapsed behind the ellipsis.
    expect(screen.queryByRole('button', { name: 'Página 10' })).not.toBeInTheDocument();
    expect(screen.getAllByText('…').length).toBeGreaterThan(0);
  });

  it('disables every control when the disabled prop is set', () => {
    render(<EmailPagination page={3} pageSize={50} total={500} onPageChange={() => {}} disabled />);
    expect(screen.getByRole('button', { name: 'Página anterior' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Página siguiente' })).toBeDisabled();
    // A non-current page number is also disabled while a fetch is in flight.
    expect(screen.getByRole('button', { name: 'Página 2' })).toBeDisabled();
  });
});
