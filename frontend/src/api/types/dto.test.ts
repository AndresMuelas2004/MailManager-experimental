import { describe, expect, it } from 'vitest';

import {
  accountOutSchema,
  attachmentMetadataSchema,
  backfillStatusListSchema,
  draftAttachmentMetadataSchema,
  draftAttachmentResponseSchema,
  draftOutSchema,
  emailContentOutSchema,
  emailMetadataOutSchema,
  emailPageSchema,
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

describe('backfillStatusListSchema', () => {
  it('parses a happy-path payload with a running job', () => {
    const parsed = backfillStatusListSchema.parse({
      accounts: [
        {
          account_id: 'a_1',
          status: 'running',
          fetched_count: 12340,
          target_total: 100000,
          done: false,
        },
      ],
      active: true,
    });
    expect(parsed.active).toBe(true);
    expect(parsed.accounts[0].status).toBe('running');
    expect(parsed.accounts[0].fetched_count).toBe(12340);
  });

  it('parses the empty / no-backfill shape', () => {
    const parsed = backfillStatusListSchema.parse({ accounts: [], active: false });
    expect(parsed.accounts).toEqual([]);
    expect(parsed.active).toBe(false);
  });

  it('rejects an unknown status enum value', () => {
    const result = backfillStatusListSchema.safeParse({
      accounts: [
        { account_id: 'a_1', status: 'paused', fetched_count: 0, target_total: 1, done: false },
      ],
      active: true,
    });
    expect(result.success).toBe(false);
  });

  it('rejects a missing active flag', () => {
    const result = backfillStatusListSchema.safeParse({ accounts: [] });
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
      mailbox_id: 'mb',
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
      mailbox_id: 'mb',
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

describe('accountOutSchema — signature_html', () => {
  const baseAccount = {
    account_id: 'acc_1',
    mailbox_id: 'mb_1',
    provider: 'gmail',
    display_label: 'Gmail',
    config: {},
    email_address: 'me@example.com',
  };

  it('parses an account carrying an HTML signature', () => {
    const parsed = accountOutSchema.parse({ ...baseAccount, signature_html: '<p>Jane</p>' });
    expect(parsed.signature_html).toBe('<p>Jane</p>');
  });

  it('parses signature_html as null (account without a signature)', () => {
    const parsed = accountOutSchema.parse({ ...baseAccount, signature_html: null });
    expect(parsed.signature_html).toBeNull();
  });

  it('rejects a missing signature_html (it is .nullable(), not .optional())', () => {
    // The backend always serialises the key; a payload that omits it is a
    // contract drift that must fail loudly instead of deserialising silently.
    const result = accountOutSchema.safeParse(baseAccount);
    expect(result.success).toBe(false);
  });

  it('rejects a non-string signature_html', () => {
    const result = accountOutSchema.safeParse({ ...baseAccount, signature_html: 123 });
    expect(result.success).toBe(false);
  });
});

describe('emailPageSchema', () => {
  const validEmail = {
    provider_message_id: 'm1',
    account_id: 'acc',
    mailbox_id: 'mb',
    thread_id: null,
    from_email: 'a@b.c',
    from_name: null,
    subject: null,
    received_at: '2024-01-01T00:00:00Z',
    is_read: false,
    box: 'ALL_MAIL',
  };

  it('parses the wrapped page envelope', () => {
    const parsed = emailPageSchema.parse({
      items: [validEmail],
      total: 1234,
      limit: 50,
      offset: 0,
    });
    expect(parsed.items).toHaveLength(1);
    expect(parsed.total).toBe(1234);
    expect(parsed.limit).toBe(50);
    expect(parsed.offset).toBe(0);
    // The inner item still benefits from the EmailMetadataOut defaults.
    expect(parsed.items[0].has_attachments).toBe(false);
    expect(parsed.items[0].is_favorite).toBe(false);
  });

  it('rejects a negative total', () => {
    const result = emailPageSchema.safeParse({ items: [], total: -1, limit: 50, offset: 0 });
    expect(result.success).toBe(false);
  });

  it('rejects a non-positive limit', () => {
    const result = emailPageSchema.safeParse({ items: [], total: 0, limit: 0, offset: 0 });
    expect(result.success).toBe(false);
  });

  it('rejects a negative offset', () => {
    const result = emailPageSchema.safeParse({ items: [], total: 0, limit: 50, offset: -50 });
    expect(result.success).toBe(false);
  });

  it('rejects a non-array items field', () => {
    const result = emailPageSchema.safeParse({ items: validEmail, total: 1, limit: 50, offset: 0 });
    expect(result.success).toBe(false);
  });

  it('rejects the legacy bare-array shape (regression guard)', () => {
    // The old contract returned ``EmailMetadataOut[]`` directly. A drift
    // back to it must fail loudly instead of being silently accepted.
    const result = emailPageSchema.safeParse([validEmail]);
    expect(result.success).toBe(false);
  });
});
