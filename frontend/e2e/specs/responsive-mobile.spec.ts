import { expect, test } from '@playwright/test';

/**
 * Mobile responsive golden path.
 *
 * The responsive layout only manifests below the 1024px (Tailwind ``lg``)
 * breakpoint and is driven entirely by CSS media queries — which jsdom does not
 * evaluate, so the integration tier (``MailboxLayoutPage.test.tsx``) can only
 * assert the drawer/FAB *wiring*, never their actual visibility. This spec is
 * the only tier that exercises the real mobile chrome in a real viewport:
 * the navigation collapses behind a hamburger, the drawer slides in and closes
 * on navigation, the floating compose button opens the full-screen composer,
 * and the email listing drops its desktop column headers in favour of cards.
 *
 * Auth uses the dev auto-login backdoor (``VITE_DEV_AUTO_LOGIN=true`` in
 * ``frontend/.env.development``): visiting an authenticated route logs the
 * seeded dev user in and the gateway redirects into their first mailbox.
 *
 * The UI language is pinned to Spanish via ``localStorage['lang']`` (the same
 * lever ``pinTestLang`` pulls in the Vitest tier) so the fixed Spanish labels
 * below resolve regardless of the browser locale Playwright launches with.
 */
test.describe('responsive (mobile viewport)', () => {
  test.use({ viewport: { width: 375, height: 812 } });

  test.beforeEach(async ({ page }) => {
    await page.addInitScript(() => {
      window.localStorage.setItem('lang', 'es');
    });
  });

  test('navigates the mailbox via hamburger drawer and FAB on a phone viewport', async ({
    page,
  }) => {
    // Dev auto-login + gateway redirect lands us inside the first mailbox.
    await page.goto('/');
    await page.waitForURL(/\/m\/[^/]+\/(inbox|sent|favorites|drafts|spam|trash)/);

    // (a) The desktop side navigation is collapsed behind the hamburger.
    const hamburger = page.getByRole('button', { name: 'Abrir menú' });
    await expect(hamburger).toBeVisible();
    await expect(hamburger).toHaveAttribute('aria-expanded', 'false');
    // The sidebar is translated off-canvas, so its links sit outside the
    // viewport. (A CSS ``transform`` keeps them technically "visible" to the
    // accessibility tree, hence ``toBeInViewport`` rather than ``toBeHidden``.)
    // "Bandeja unificada" is the inbox NavLink label.
    const inboxLink = page.getByRole('link', { name: 'Bandeja unificada' });
    await expect(inboxLink).not.toBeInViewport();

    // (b) Tapping the hamburger slides the drawer into the viewport.
    await hamburger.click();
    await expect(hamburger).toHaveAttribute('aria-expanded', 'true');
    await expect(inboxLink).toBeInViewport();

    // (c) Tapping a nav link navigates and closes the drawer.
    await page.getByRole('link', { name: 'Enviados' }).click();
    await page.waitForURL(/\/m\/[^/]+\/sent/);
    await expect(hamburger).toHaveAttribute('aria-expanded', 'false');
    await expect(inboxLink).not.toBeInViewport();

    // (d) The floating compose button opens the full-screen composer.
    // ``Redactar`` labels both the FAB and the sidebar's round button; the FAB
    // renders last in document order (the sidebar comes first), so ``.last()``
    // targets the FAB unambiguously.
    await page.getByRole('button', { name: 'Redactar' }).last().click();
    await expect(page.getByRole('heading', { name: 'Nuevo mensaje' })).toBeVisible();
    // Close the composer to return to the listing chrome. ``Cerrar`` also labels
    // the (off-canvas) drawer X; the composer's X renders last.
    await page.getByRole('button', { name: 'Cerrar' }).last().click();
    await expect(page.getByRole('heading', { name: 'Nuevo mensaje' })).toBeHidden();

    // (e) The listing renders as cards: the desktop column-header row
    // (``hidden lg:flex``) is not visible on a phone viewport. "Remitente" is
    // always present in that header; "Para"/"De" are the plan's load-bearing
    // pair (present only in views that show those columns — ``toBeHidden``
    // passes whether the header cell is absent or merely hidden).
    await expect(page.getByText('Remitente', { exact: true })).toBeHidden();
    await expect(page.getByText('Para', { exact: true })).toBeHidden();
    await expect(page.getByText('De', { exact: true })).toBeHidden();
  });
});
