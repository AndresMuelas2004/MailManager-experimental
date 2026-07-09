import { describe, expect, it } from 'vitest';

import {
  BLOCKED_EXTENSIONS,
  MAX_ATTACHMENT_SIZE,
  MAX_ATTACHMENTS_PER_MESSAGE,
  MAX_MESSAGE_SIZE,
  formatBytes,
  humaniseAttachmentError,
  isBlockedExtension,
  validateFileForUpload,
} from './attachments';

function makeFile(name: string, sizeBytes: number): File {
  // jsdom's File can be constructed with a single Uint8Array shard. Size is
  // determined by the byte length of the chunks, not by a constructor flag.
  return new File([new Uint8Array(sizeBytes)], name, { type: 'application/octet-stream' });
}

describe('isBlockedExtension', () => {
  it.each(['malware.exe', 'script.BAT', 'macro.Vbs', 'payload.js', 'installer.msi'])(
    'blocks %s',
    (filename) => {
      expect(isBlockedExtension(filename)).toBe(true);
    },
  );

  it.each(['report.pdf', 'image.png', 'archive.zip', 'doc.docx'])('allows %s', (filename) => {
    expect(isBlockedExtension(filename)).toBe(false);
  });

  it('returns false for empty string', () => {
    expect(isBlockedExtension('')).toBe(false);
  });

  it('returns false for files without an extension', () => {
    expect(isBlockedExtension('Makefile')).toBe(false);
  });

  it('returns false when the trailing dot has no suffix', () => {
    expect(isBlockedExtension('weird.')).toBe(false);
  });

  it('uses only the last dot for compound extensions', () => {
    // foo.txt.exe is blocked (last segment is exe).
    expect(isBlockedExtension('foo.txt.exe')).toBe(true);
    // foo.exe.txt is allowed (last segment is txt).
    expect(isBlockedExtension('foo.exe.txt')).toBe(false);
  });

  it('handles unicode filenames', () => {
    expect(isBlockedExtension('Información.exe')).toBe(true);
    expect(isBlockedExtension('Información.pdf')).toBe(false);
  });
});

describe('BLOCKED_EXTENSIONS constant', () => {
  it('is non-empty and lowercase only', () => {
    expect(BLOCKED_EXTENSIONS.size).toBeGreaterThan(0);
    for (const ext of BLOCKED_EXTENSIONS) {
      expect(ext).toBe(ext.toLowerCase());
      expect(ext.startsWith('.')).toBe(false);
    }
  });
});

describe('formatBytes', () => {
  it('renders bytes under 1 KB as "N B"', () => {
    expect(formatBytes(0)).toBe('0 B');
    expect(formatBytes(512)).toBe('512 B');
    expect(formatBytes(1023)).toBe('1023 B');
  });

  it('renders KB without decimals', () => {
    expect(formatBytes(1024)).toBe('1 KB');
    expect(formatBytes(1024 * 500)).toBe('500 KB');
  });

  it('renders MB with one decimal up to 9.9 MB and uses comma', () => {
    expect(formatBytes(1024 * 1024 * 5)).toBe('5,0 MB');
    expect(formatBytes(1024 * 1024 * 1.5)).toBe('1,5 MB');
  });

  it('drops the decimal at 10 MB and above', () => {
    expect(formatBytes(1024 * 1024 * 10)).toBe('10 MB');
    expect(formatBytes(1024 * 1024 * 100)).toBe('100 MB');
  });
});

describe('validateFileForUpload', () => {
  it('returns ok for a small allowed file', () => {
    const file = makeFile('report.pdf', 100);
    const result = validateFileForUpload(file, 0, 0);
    expect(result).toEqual({ ok: true });
  });

  it('rejects when count is at the limit', () => {
    const file = makeFile('ok.pdf', 100);
    const result = validateFileForUpload(file, 0, MAX_ATTACHMENTS_PER_MESSAGE);
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.reason).toBe('limit_exceeded');
    }
  });

  it('rejects blocked extensions before size checks', () => {
    // Even a tiny file is rejected if the extension is blocked.
    const file = makeFile('virus.exe', 1);
    const result = validateFileForUpload(file, 0, 0);
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.reason).toBe('blocked_extension');
    }
  });

  it('rejects files larger than 25 MB per file', () => {
    const file = makeFile('big.pdf', MAX_ATTACHMENT_SIZE + 1);
    const result = validateFileForUpload(file, 0, 0);
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.reason).toBe('too_large');
    }
  });

  it('rejects when adding the file would exceed 25 MB total', () => {
    const file = makeFile('add.pdf', 5 * 1024 * 1024);
    // Existing 22 MB + new 5 MB = 27 MB > 25 MB cap.
    const result = validateFileForUpload(file, 22 * 1024 * 1024, 1);
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.reason).toBe('message_size_exceeded');
    }
  });

  it('priority: limit_exceeded checked first', () => {
    // Even a blocked file at the count cap returns limit_exceeded —
    // matches the production order so the user sees the dominant reason
    // (the count) instead of the more specific extension reason.
    const file = makeFile('virus.exe', 1);
    const result = validateFileForUpload(file, 0, MAX_ATTACHMENTS_PER_MESSAGE);
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.reason).toBe('limit_exceeded');
    }
  });

  it('priority: blocked_extension before too_large', () => {
    // A blocked extension on an oversize file: the extension wins.
    const file = makeFile('virus.exe', MAX_ATTACHMENT_SIZE + 1);
    const result = validateFileForUpload(file, 0, 0);
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.reason).toBe('blocked_extension');
    }
  });

  it('boundary: file exactly at 25 MB is allowed', () => {
    const file = makeFile('boundary.pdf', MAX_ATTACHMENT_SIZE);
    const result = validateFileForUpload(file, 0, 0);
    expect(result).toEqual({ ok: true });
  });

  it('boundary: cumulative exactly at 25 MB is allowed', () => {
    const file = makeFile('add.pdf', 5 * 1024 * 1024);
    const result = validateFileForUpload(file, MAX_MESSAGE_SIZE - 5 * 1024 * 1024, 1);
    expect(result).toEqual({ ok: true });
  });
});

describe('humaniseAttachmentError', () => {
  it.each([
    'attachment_blocked_extension',
    'attachment_too_large',
    'attachment_message_size_exceeded',
    'attachment_limit_exceeded',
    'attachment_unavailable',
    'provider_forbidden',
    'provider_unavailable',
    'request_too_large',
  ])('returns a non-empty message for %s', (code) => {
    const msg = humaniseAttachmentError(code);
    expect(msg).not.toBeNull();
    expect(typeof msg).toBe('string');
    expect((msg ?? '').length).toBeGreaterThan(0);
  });

  it('returns null for unknown codes so the caller falls back to toUiError', () => {
    expect(humaniseAttachmentError('unknown_code_xyz')).toBeNull();
    expect(humaniseAttachmentError(undefined)).toBeNull();
  });

  it('attachment_send_failed reports the failed-attachment count when present', () => {
    const msg = humaniseAttachmentError('attachment_send_failed', {
      failed_attachments: [{ filename: 'a' }, { filename: 'b' }],
    });
    expect(msg).toMatch(/2/);
  });

  it('attachment_send_failed falls back to a generic message without detail', () => {
    const msg = humaniseAttachmentError('attachment_send_failed');
    expect(msg).not.toBeNull();
  });
});
