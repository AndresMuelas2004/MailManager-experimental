import { lazy } from 'react';
import { createBrowserRouter, Navigate, RouterProvider } from 'react-router-dom';

import RequireAuth from './RequireAuth';
import RootLayout from '../layout/RootLayout';
import LoginPage from '../../features/auth/pages/LoginPage';
import LandingPage from '../../features/landing/pages/LandingPage';
import MailboxGatewayPage from '../../features/mailboxes/pages/MailboxGatewayPage';
import MailboxLayoutPage from '../../features/mailboxes/pages/MailboxLayoutPage';
import DraftComposerMount from '../../features/drafts/pages/DraftComposerMount';

const PrivacyPage = lazy(() => import('../../features/landing/pages/PrivacyPage'));
const TermsPage = lazy(() => import('../../features/landing/pages/TermsPage'));
const CreateMailboxPage = lazy(() => import('../../features/mailboxes/pages/CreateMailboxPage'));
const ConnectedAccountsPage = lazy(
  () => import('../../features/accounts/pages/ConnectedAccountsPage'),
);
const SignatureSettingsPage = lazy(
  () => import('../../features/accounts/pages/SignatureSettingsPage'),
);
const UnifiedInboxPage = lazy(() => import('../../features/emails/pages/UnifiedInboxPage'));
const AccountInboxPage = lazy(() => import('../../features/emails/pages/AccountInboxPage'));
const DraftsPage = lazy(() => import('../../features/drafts/pages/DraftsPage'));
const AccountDraftsPage = lazy(() => import('../../features/drafts/pages/AccountDraftsPage'));
const FavoritesPage = lazy(() => import('../../features/emails/pages/FavoritesPage'));
const AccountFavoritesPage = lazy(() => import('../../features/emails/pages/AccountFavoritesPage'));
const VirtualMailboxesPage = lazy(() => import('../../features/emails/pages/VirtualMailboxesPage'));
const VirtualMailboxViewPage = lazy(
  () => import('../../features/emails/pages/VirtualMailboxViewPage'),
);
const FoldersPage = lazy(() => import('../../features/folders/pages/FoldersPage'));
// The folder VIEW lives in features/emails (it reuses EmailTable — a feature
// cannot import another feature's components); the folder CRUD + rules stay in
// features/folders.
const FolderViewPage = lazy(() => import('../../features/emails/pages/FolderViewPage'));
const RulesSettingsPage = lazy(() => import('../../features/folders/pages/RulesSettingsPage'));
const SettingsLayoutPage = lazy(() => import('../../features/settings/pages/SettingsLayoutPage'));
const SettingsAccountPage = lazy(() => import('../../features/settings/pages/SettingsAccountPage'));
const PreferencesPage = lazy(() => import('../../features/settings/pages/PreferencesPage'));
const DataSyncPage = lazy(() => import('../../features/settings/pages/DataSyncPage'));
const AboutPage = lazy(() => import('../../features/settings/pages/AboutPage'));
const MailboxesSettingsPage = lazy(
  () => import('../../features/mailboxes/pages/MailboxesSettingsPage'),
);

const router = createBrowserRouter([
  {
    element: <RootLayout />,
    children: [
      { path: '/login', element: <LoginPage /> },
      { path: '/privacy', element: <PrivacyPage /> },
      { path: '/terms', element: <TermsPage /> },
      {
        path: '/',
        children: [
          // Public index: anonymous visitors get the marketing landing, and
          // the page itself forwards authenticated ones to /home — so every
          // pre-existing navigate('/') / to="/" keeps working unchanged.
          { index: true, element: <LandingPage /> },
          {
            element: <RequireAuth />,
            children: [
              { path: 'home', element: <MailboxGatewayPage /> },
              { path: 'create-mailbox', element: <CreateMailboxPage /> },
              {
                path: 'm/:mailboxId',
                element: <MailboxLayoutPage />,
                children: [
                  // Pathless layout that keeps the singleton draft composer mounted
                  // across every mailbox content route. Lives in features/drafts so
                  // the cross-feature import of DraftComposerHost is avoided.
                  {
                    element: <DraftComposerMount />,
                    children: [
                      { path: 'inbox', element: <UnifiedInboxPage box="ALL_MAIL" /> },
                      { path: 'sent', element: <UnifiedInboxPage box="SENT" /> },
                      { path: 'archive', element: <UnifiedInboxPage box="ARCHIVE" /> },
                      { path: 'spam', element: <UnifiedInboxPage box="SPAM" /> },
                      { path: 'trash', element: <UnifiedInboxPage box="TRASH" /> },
                      { path: 'drafts', element: <DraftsPage /> },
                      { path: 'favorites', element: <FavoritesPage /> },
                      { path: 'virtual-mailboxes', element: <VirtualMailboxesPage /> },
                      {
                        path: 'virtual-mailboxes/:virtualMailboxId',
                        element: <VirtualMailboxViewPage />,
                      },
                      { path: 'folders', element: <FoldersPage /> },
                      { path: 'folders/:folderId', element: <FolderViewPage /> },
                      {
                        path: 'account/:accountId',
                        children: [
                          { index: true, element: <Navigate to="inbox" replace /> },
                          { path: 'inbox', element: <AccountInboxPage box="ALL_MAIL" /> },
                          { path: 'sent', element: <AccountInboxPage box="SENT" /> },
                          { path: 'favorites', element: <AccountFavoritesPage /> },
                          { path: 'archive', element: <AccountInboxPage box="ARCHIVE" /> },
                          { path: 'spam', element: <AccountInboxPage box="SPAM" /> },
                          { path: 'trash', element: <AccountInboxPage box="TRASH" /> },
                          { path: 'drafts', element: <AccountDraftsPage /> },
                        ],
                      },
                    ],
                  },
                  // Settings area — sibling of DraftComposerMount (not a mail view,
                  // so it does not need the composer singleton mounted). The
                  // ConnectedAccountsPage reused here is the same lazy component the
                  // old standalone /accounts route used.
                  {
                    path: 'settings',
                    element: <SettingsLayoutPage />,
                    children: [
                      { index: true, element: <SettingsAccountPage /> },
                      { path: 'accounts', element: <ConnectedAccountsPage /> },
                      { path: 'signature', element: <SignatureSettingsPage /> },
                      { path: 'mailboxes', element: <MailboxesSettingsPage /> },
                      { path: 'rules', element: <RulesSettingsPage /> },
                      { path: 'preferences', element: <PreferencesPage /> },
                      { path: 'data', element: <DataSyncPage /> },
                      { path: 'about', element: <AboutPage /> },
                    ],
                  },
                ],
              },
            ],
          },
        ],
      },
    ],
  },
]);

export default function AppRouter() {
  return <RouterProvider router={router} />;
}
