/**
 * Unit tests for the analytics consent + tagging helpers.
 *
 * The measurement id is a build-time constant read at module load, so the tests
 * that need a configured property stub ``import.meta.env`` and re-import the
 * module through ``vi.resetModules()`` — the same trick used for any Vite env
 * constant. The rest exercise jsdom's localStorage / document directly, as
 * ``lib/lastSync.test.ts`` does.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  isTrackedPath,
  readAnalyticsConsent,
  trackPageView,
  writeAnalyticsConsent,
} from './analytics';

async function importWithMeasurementId(id: string) {
  vi.resetModules();
  vi.stubEnv('VITE_GA_MEASUREMENT_ID', id);
  return import('./analytics');
}

describe('isTrackedPath', () => {
  it.each(['/', '/privacy', '/terms', '/login'])('accepts the public page %s', (path) => {
    expect(isTrackedPath(path)).toBe(true);
  });

  it('normalises a trailing slash', () => {
    expect(isTrackedPath('/privacy/')).toBe(true);
  });

  it.each([
    '/home',
    '/m/mb_1/inbox',
    '/m/mb_1/folders/f_1',
    '/m/mb_1/settings/accounts',
    '/create-mailbox',
  ])('rejects the in-app route %s', (path) => {
    expect(isTrackedPath(path)).toBe(false);
  });
});

describe('analytics consent storage', () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  afterEach(() => {
    window.localStorage.clear();
    vi.restoreAllMocks();
  });

  it('returns null before the visitor has answered', () => {
    expect(readAnalyticsConsent()).toBeNull();
  });

  it.each(['granted', 'denied'] as const)('round-trips the %s choice', (choice) => {
    writeAnalyticsConsent(choice);
    expect(readAnalyticsConsent()).toBe(choice);
  });

  it('returns null when the stored value is not a recognised choice', () => {
    window.localStorage.setItem('analyticsConsent', 'maybe');
    expect(readAnalyticsConsent()).toBeNull();
  });

  it('returns null when localStorage.getItem throws (private mode)', () => {
    vi.spyOn(window.localStorage, 'getItem').mockImplementation(() => {
      throw new Error('storage unavailable');
    });
    expect(readAnalyticsConsent()).toBeNull();
  });

  it('swallows a localStorage.setItem failure instead of propagating it', () => {
    vi.spyOn(window.localStorage, 'setItem').mockImplementation(() => {
      throw new Error('quota exceeded');
    });
    expect(() => writeAnalyticsConsent('granted')).not.toThrow();
  });
});

describe('loadAnalytics', () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    document.head.querySelectorAll('script').forEach((node) => node.remove());
    delete window.gtag;
    delete window.dataLayer;
  });

  it('injects no script when no measurement id was baked into the bundle', async () => {
    const analytics = await importWithMeasurementId('');
    analytics.loadAnalytics();
    expect(analytics.isAnalyticsConfigured()).toBe(false);
    expect(document.head.querySelector('script')).toBeNull();
    expect(window.gtag).toBeUndefined();
  });

  it('injects gtag.js once and stays idempotent across repeated calls', async () => {
    const analytics = await importWithMeasurementId('G-TEST12345');
    analytics.loadAnalytics();
    analytics.loadAnalytics();

    const scripts = document.head.querySelectorAll('script[src*="googletagmanager.com/gtag/js"]');
    expect(scripts).toHaveLength(1);
    expect(scripts[0].getAttribute('src')).toContain('id=G-TEST12345');
  });

  it('configures the property with send_page_view disabled', async () => {
    const analytics = await importWithMeasurementId('G-TEST12345');
    analytics.loadAnalytics();

    const configCall = (window.dataLayer ?? []).find(
      (entry) => Array.isArray(entry) && entry[0] === 'config',
    ) as unknown[] | undefined;
    expect(configCall?.[1]).toBe('G-TEST12345');
    expect(configCall?.[2]).toEqual({ send_page_view: false });
  });
});

describe('trackPageView', () => {
  afterEach(() => {
    delete window.gtag;
  });

  it('does nothing when analytics was never loaded (no consent)', () => {
    expect(() => trackPageView('/privacy')).not.toThrow();
  });

  it('reports a public page', () => {
    const gtag = vi.fn();
    window.gtag = gtag;
    trackPageView('/privacy');
    expect(gtag).toHaveBeenCalledWith(
      'event',
      'page_view',
      expect.objectContaining({ page_path: '/privacy' }),
    );
  });

  it('never reports an in-app route even once loaded', () => {
    const gtag = vi.fn();
    window.gtag = gtag;
    trackPageView('/m/mb_1/inbox');
    expect(gtag).not.toHaveBeenCalled();
  });
});
