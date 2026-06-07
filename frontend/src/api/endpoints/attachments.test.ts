/**
 * API-level tests for ``downloadEmailAttachment`` — the received-attachment
 * download fix.
 *
 * These exercise the public API surface (``downloadEmailAttachment`` →
 * ``requestBlob``) with MSW synthesising the binary response at the
 * ``fetch`` boundary. The module-local ``parseFilenameFromContentDisposition``
 * is covered indirectly and legitimately through the resolved ``filename``
 * (a field of the public ``{ blob, filename }`` contract) — no internals are
 * spied (test/CLAUDE.md §4, anti-pattern §9.1).
 *
 * The second case captures the exact bug that was fixed: when the browser
 * cannot read ``Content-Disposition`` (cross-origin without the CORS header
 * exposed), the download must fall back to the real ``filename`` from the
 * attachment metadata — NOT to the synthetic ``attachment-<uuid>``.
 */

import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';

import { server } from '../../test/msw/server';
import { downloadEmailAttachment } from './attachments';

const API_BASE = 'http://localhost:8000';
const DOWNLOAD_PATH = `${API_BASE}/mailboxes/:mailboxId/accounts/:accountId/emails/:pmid/attachments/:attachmentId`;

describe('downloadEmailAttachment', () => {
  it('uses the Content-Disposition filename when the header is present', async () => {
    server.use(
      http.get(
        DOWNLOAD_PATH,
        () =>
          new HttpResponse(new Blob(['binary'], { type: 'application/pdf' }), {
            headers: {
              'Content-Type': 'application/pdf',
              'Content-Disposition':
                'attachment; filename="contrato.pdf"; filename*=UTF-8\'\'contrato.pdf',
            },
          }),
      ),
    );

    const { filename } = await downloadEmailAttachment(
      'mb1',
      'acc1',
      'pm1',
      'att1',
      // A deliberately different fallback so we can prove the header wins.
      'attachment-att1',
    );

    expect(filename).toBe('contrato.pdf');
  });

  it('falls back to the metadata filename when Content-Disposition is missing', async () => {
    // Simulates the cross-origin case where the browser cannot read the
    // header. Before the fix the fallback was ``attachment-<uuid>``; now it
    // is the real attachment filename, so the file keeps its name+extension.
    server.use(
      http.get(
        DOWNLOAD_PATH,
        () =>
          new HttpResponse(new Blob(['binary'], { type: 'application/octet-stream' }), {
            headers: { 'Content-Type': 'application/octet-stream' },
          }),
      ),
    );

    const { filename } = await downloadEmailAttachment(
      'mb1',
      'acc1',
      'pm1',
      'att1',
      'contrato.pdf',
    );

    expect(filename).toBe('contrato.pdf');
    expect(filename).not.toContain('attachment-att1');
  });
});
