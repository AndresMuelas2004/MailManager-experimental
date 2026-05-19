import { describe, expect, it } from 'vitest';

import {
  attachmentMetadataSchema,
  draftAttachmentMetadataSchema,
  draftAttachmentResponseSchema,
  draftOutSchema,
  emailContentOutSchema,
  emailMetadataOutSchema,
  failedAttachmentSchema,
} from './dto';

const VALID_UUID = '11111111-1111-4000-a000-111111111111';

// Schemas guard the wire contract (snake_case, nullability). The unit
// scope keeps the assertions to "valid input parses, malformed input
// fails" — anything richer (HTTP behavior, integration with React Query)
// belongs in the integration layer that exercises MSW.

describe('attachmentMetadataSchema', () => {
  it('parses a happy-path payload', () => {
    const parsed = attachmentMetadataSchema.parse({
      attachment_id: VALID_UUID,
      filename: 'report.pdf',
      mime_type: 'application/pdf',
      size: 1024,
      is_downloaded: false,
      is_unavailable: false,
      position: 0,
    });
    expect(parsed.attachment_id).toBe(VALID_UUID);
    expect(parsed.filename).toBe('report.pdf');
    expect(parsed.is_downloaded).toBe(false);
  });

  it('rejects a non-uuid attachment_id', () => {
    const result = attachmentMetadataSchema.safeParse({
      attachment_id: 'not-a-uuid',
      filename: 'x.pdf',
      mime_type: 'application/pdf',
      size: 1,
      is_downloaded: false,
      is_unavailable: false,
      position: 0,
    });
    expect(result.success).toBe(false);
  });

  it('rejects negative size', () => {
    const result = attachmentMetadataSchema.safeParse({
      attachment_id: VALID_UUID,
      filename: 'x.pdf',
      mime_type: 'application/pdf',
      size: -1,
      is_downloaded: false,
      is_unavailable: false,
      position: 0,
    });
    expect(result.success).toBe(false);
  });

  it('rejects missing required fields', () => {
    const result = attachmentMetadataSchema.safeParse({
      attachment_id: VALID_UUID,
      filename: 'x.pdf',
      // mime_type missing
      size: 1,
      is_downloaded: false,
      is_unavailable: false,
      position: 0,
    });
    expect(result.success).toBe(false);
  });
});

describe('draftAttachmentMetadataSchema', () => {
  it('parses with provider_attachment_id null (D-07 lazy push)', () => {
    const parsed = draftAttachmentMetadataSchema.parse({
      draft_attachment_id: VALID_UUID,
      filename: 'a.pdf',
      mime_type: 'application/pdf',
      size: 1,
      position: 0,
      provider_attachment_id: null,
    });
    expect(parsed.provider_attachment_id).toBeNull();
  });

  it('parses with provider_attachment_id populated (D-27 partial-success retry)', () => {
    const parsed = draftAttachmentMetadataSchema.parse({
      draft_attachment_id: VALID_UUID,
      filename: 'a.pdf',
      mime_type: 'application/pdf',
      size: 1,
      position: 0,
      provider_attachment_id: 'graph-att-xyz',
    });
    expect(parsed.provider_attachment_id).toBe('graph-att-xyz');
  });

  it('rejects undefined provider_attachment_id (must be string | null, not optional)', () => {
    const result = draftAttachmentMetadataSchema.safeParse({
      draft_attachment_id: VALID_UUID,
      filename: 'a.pdf',
      mime_type: 'application/pdf',
      size: 1,
      position: 0,
      // provider_attachment_id missing
    });
    expect(result.success).toBe(false);
  });
});

describe('draftAttachmentResponseSchema', () => {
  it('is structurally identical to draftAttachmentMetadataSchema', () => {
    // The aliasing is documented in attachment.py — both endpoints
    // share the response shape. A drift here would mean the create
    // endpoint diverged from the metadata one without the test noticing.
    const payload = {
      draft_attachment_id: VALID_UUID,
      filename: 'a.pdf',
      mime_type: 'application/pdf',
      size: 1,
      position: 0,
      provider_attachment_id: null,
    };
    expect(draftAttachmentResponseSchema.parse(payload)).toEqual(
      draftAttachmentMetadataSchema.parse(payload),
    );
  });
});

describe('emailMetadataOutSchema — has_attachments', () => {
  it('defaults has_attachments to false when the field is missing (B.lazy)', () => {
    // Older payloads or backends that have not yet shipped the column
    // must keep deserialising — the icon clip stays off until the email
    // is opened. ``.default(false)`` is the contract that enables this.
    const parsed = emailMetadataOutSchema.parse({
      provider_message_id: 'm1',
      account_id: 'acc',
      thread_id: null,
      from_email: 'a@b.c',
      from_name: null,
      subject: null,
      received_at: '2024-01-01T00:00:00Z',
      is_read: false,
      box: 'ALL_MAIL',
    });
    expect(parsed.has_attachments).toBe(false);
  });

  it('honours has_attachments=true when the backend sets it', () => {
    const parsed = emailMetadataOutSchema.parse({
      provider_message_id: 'm1',
      account_id: 'acc',
      thread_id: null,
      from_email: 'a@b.c',
      from_name: null,
      subject: null,
      received_at: '2024-01-01T00:00:00Z',
      is_read: false,
      box: 'ALL_MAIL',
      has_attachments: true,
    });
    expect(parsed.has_attachments).toBe(true);
  });
});

describe('emailContentOutSchema — attachments', () => {
  it('defaults attachments to [] when the field is missing', () => {
    const parsed = emailContentOutSchema.parse({
      html_body: '<p>x</p>',
      text_body: null,
    });
    expect(parsed.attachments).toEqual([]);
  });

  it('parses a populated attachments list', () => {
    const parsed = emailContentOutSchema.parse({
      html_body: null,
      text_body: 'plain',
      attachments: [
        {
          attachment_id: VALID_UUID,
          filename: 'a.pdf',
          mime_type: 'application/pdf',
          size: 10,
          is_downloaded: false,
          is_unavailable: false,
          position: 0,
        },
      ],
    });
    expect(parsed.attachments).toHaveLength(1);
    expect(parsed.attachments[0].filename).toBe('a.pdf');
  });
});

describe('draftOutSchema — body + attachments', () => {
  it('parses body as plain text (D-31) and the new attachments list', () => {
    const parsed = draftOutSchema.parse({
      provider_draft_id: 'draft1',
      account_id: 'acc',
      to_recipients: ['to@x'],
      cc_recipients: [],
      bcc_recipients: [],
      subject: 'hi',
      body: 'plain body',
      created_at: '2024-01-01T00:00:00Z',
      updated_at: '2024-01-01T00:00:00Z',
      attachments: [
        {
          draft_attachment_id: VALID_UUID,
          filename: 'a.pdf',
          mime_type: 'application/pdf',
          size: 10,
          position: 0,
          provider_attachment_id: null,
        },
      ],
    });
    expect(parsed.body).toBe('plain body');
    expect(parsed.attachments).toHaveLength(1);
  });

  it('attachments default to [] when the backend omits them', () => {
    const parsed = draftOutSchema.parse({
      provider_draft_id: 'draft1',
      account_id: 'acc',
      to_recipients: [],
      cc_recipients: [],
      bcc_recipients: [],
      subject: '',
      body: '',
      created_at: '2024-01-01T00:00:00Z',
      updated_at: '2024-01-01T00:00:00Z',
    });
    expect(parsed.attachments).toEqual([]);
  });
});

describe('failedAttachmentSchema', () => {
  it('parses the partial-failure envelope shape', () => {
    const parsed = failedAttachmentSchema.parse({
      draft_attachment_id: 'a1',
      filename: 'broken.pdf',
      reason: 'upload_session_failed',
    });
    expect(parsed.reason).toBe('upload_session_failed');
    // The id is intentionally NOT typed as uuid — when this envelope
    // surfaces, the local id may have been generated client-side as
    // a tmp-* string.
    expect(parsed.draft_attachment_id).toBe('a1');
  });

  it('rejects non-string reason', () => {
    const result = failedAttachmentSchema.safeParse({
      draft_attachment_id: 'a1',
      filename: 'broken.pdf',
      reason: 123,
    });
    expect(result.success).toBe(false);
  });
});
