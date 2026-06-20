import AuthProvider from './AuthProvider';
import QueryProvider from './QueryProvider';
import { I18nProvider } from '../../lib/i18n';
import AppRouter from '../routes/router';

export default function Providers() {
  return (
    <QueryProvider>
      <AuthProvider>
        <I18nProvider>
          <AppRouter />
        </I18nProvider>
      </AuthProvider>
    </QueryProvider>
  );
}
