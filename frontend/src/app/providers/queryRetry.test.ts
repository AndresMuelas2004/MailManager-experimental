import { describe, expect, it } from 'vitest';

import { ApiError } from '../../api/client/errors';
import { shouldRetryQuery } from './queryRetry';

describe('shouldRetryQuery', () => {
  it('never retries a 429 rate-limit error, regardless of failureCount', () => {
    const err = new ApiError('slow down', 'rate_limit_exceeded', 429);
    expect(shouldRetryQuery(0, err)).toBe(false);
    expect(shouldRetryQuery(5, err)).toBe(false);
  });

  it('retries a non-429 4xx ApiError exactly once', () => {
    for (const status of [403, 404, 422]) {
      const err = new ApiError('nope', 'some_code', status);
      expect(shouldRetryQuery(0, err)).toBe(true);
      expect(shouldRetryQuery(1, err)).toBe(false);
    }
  });

  it('retries a 5xx ApiError exactly once', () => {
    const err = new ApiError('boom', 'http_error', 500);
    expect(shouldRetryQuery(0, err)).toBe(true);
    expect(shouldRetryQuery(1, err)).toBe(false);
  });

  it('retries a non-ApiError failure exactly once', () => {
    const err = new Error('boom');
    expect(shouldRetryQuery(0, err)).toBe(true);
    expect(shouldRetryQuery(1, err)).toBe(false);
  });
});
