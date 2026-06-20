import { lazy } from 'react';
import { createBrowserRouter, Navigate, RouterProvider } from 'react-router-dom';

import RequireAuth from './RequireAuth';
import RootLayout from '../layout/RootLayout';
import LoginPage from '../../features/auth/pages/LoginPage';
import MailboxGatewayPage from '../../features/mailboxes/pages/MailboxGatewayPage';
import MailboxLayoutPage from '../../features/mailboxes/pages/MailboxLayoutPage';
import DraftComposerMount from '../../features/drafts/pages/DraftComposerMount';

const CreateMailboxPage = lazy(() => import('../../features/mailboxes/pages/CreateMailboxPage'));
const ConnectedAccountsPage = lazy(
  () => import('../../features/accounts/pages/ConnectedAccountsPage'),
);
const UnifiedInboxPage = lazy(() => import('../../features/emails/pages/UnifiedInboxPage'));
const AccountInboxPage = lazy(() => import('../../features/emails/pages/AccountInboxPage'));
const DraftsPage = lazy(() => import('../../features/drafts/pages/DraftsPage'));
const AccountDraftsPage = lazy(() => import('../../features/drafts/pages/AccountDraftsPage'));
const FavoritesPage = lazy(() => import('../../features/emails/pages/FavoritesPage'));
const VirtualMailboxesPage = lazy(() => import('../../features/emails/pages/VirtualMailboxesPage'));
const VirtualMailboxViewPage = lazy(
  () => import('../../features/emails/pages/VirtualMailboxViewPage'),
);
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
      {
        path: '/',
        element: <RequireAuth />,
        children: [
          { index: true, element: <MailboxGatewayPage /> },
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
                  { path: 'spam', element: <UnifiedInboxPage box="SPAM" /> },
                  { path: 'trash', element: <UnifiedInboxPage box="TRASH" /> },
                  { path: 'drafts', element: <DraftsPage /> },
                  { path: 'favorites', element: <FavoritesPage /> },
                  { path: 'virtual-mailboxes', element: <VirtualMailboxesPage /> },
                  {
                    path: 'virtual-mailboxes/:virtualMailboxId',
                    element: <VirtualMailboxViewPage />,
                  },
                  {
                    path: 'account/:accountId',
                    children: [
                      { index: true, element: <Navigate to="inbox" replace /> },
                      { path: 'inbox', element: <AccountInboxPage box="ALL_MAIL" /> },
                      { path: 'sent', element: <AccountInboxPage box="SENT" /> },
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
                  { path: 'mailboxes', element: <MailboxesSettingsPage /> },
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
]);

export default function AppRouter() {
  return <RouterProvider router={router} />;
}
