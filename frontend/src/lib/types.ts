export type EmailBox = 'ALL_MAIL' | 'SENT' | 'SPAM' | 'TRASH';

export type ComposerMode =
  | 'new_email'
  | 'new_draft'
  | 'edit_draft'
  | 'reply'
  | 'reply_all'
  | 'forward';

export type ReplyKind = 'reply' | 'reply_all' | 'forward';
