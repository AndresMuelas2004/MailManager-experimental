import { Outlet, useParams } from 'react-router-dom';

import DraftComposerHost from '../components/DraftComposerHost';

// Pathless layout route that mounts the singleton draft-composer host next to
// the mailbox content <Outlet/>. It lives in features/drafts/pages so the host
// (features/drafts/components) is imported intra-feature: the cross-feature
// rule forbids features/mailboxes from importing features/drafts, while the
// router (app/routes) may import features/*/pages but not features/*/components.
// Sitting above every m/:mailboxId content route keeps the composer mounted as
// a singleton across navigations within the mailbox shell — the same lifetime
// it had when MailboxLayoutPage rendered the host directly.
export default function DraftComposerMount() {
  const { mailboxId } = useParams<{ mailboxId: string }>();
  return (
    <>
      <Outlet />
      <DraftComposerHost mailboxId={mailboxId ?? null} />
    </>
  );
}
