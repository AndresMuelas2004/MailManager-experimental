import { Suspense } from 'react';
import { Outlet } from 'react-router-dom';

import DraftComposerProvider from '../providers/DraftComposerProvider';
import Spinner from '../../components/common/Spinner';

export default function RootLayout() {
  return (
    <Suspense
      fallback={
        <div className="flex min-h-screen items-center justify-center">
          <Spinner />
        </div>
      }
    >
      <DraftComposerProvider>
        <Outlet />
      </DraftComposerProvider>
    </Suspense>
  );
}
