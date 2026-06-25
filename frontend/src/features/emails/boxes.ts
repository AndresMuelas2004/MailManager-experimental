import type { EmailBox } from '../../lib/types';
import type { BulkAction } from './types';

export type EmailBoxConfig = {
  // i18n keys (resolved with ``t`` by the consuming page). Stored as keys, not
  // literals, so this static config stays language-agnostic.
  titleKey: string;
  subtitleKey: string;
  allowedBulkActions: BulkAction[];
};

export const EMAIL_BOX_CONFIG: Record<EmailBox, EmailBoxConfig> = {
  ALL_MAIL: {
    titleKey: 'boxes.allMailTitle',
    subtitleKey: 'boxes.allMailSubtitle',
    allowedBulkActions: ['toggle_read', 'archive', 'move_to_trash', 'mark_spam'],
  },
  SENT: {
    titleKey: 'boxes.sentTitle',
    subtitleKey: 'boxes.sentSubtitle',
    allowedBulkActions: ['toggle_read', 'move_to_trash'],
  },
  SPAM: {
    titleKey: 'boxes.spamTitle',
    subtitleKey: 'boxes.spamSubtitle',
    allowedBulkActions: ['toggle_read', 'move_to_trash', 'restore_from_spam'],
  },
  TRASH: {
    titleKey: 'boxes.trashTitle',
    subtitleKey: 'boxes.trashSubtitle',
    allowedBulkActions: ['toggle_read', 'restore_from_trash', 'delete_permanently'],
  },
  ARCHIVE: {
    titleKey: 'boxes.archiveTitle',
    subtitleKey: 'boxes.archiveSubtitle',
    allowedBulkActions: ['toggle_read', 'unarchive', 'move_to_trash'],
  },
};
