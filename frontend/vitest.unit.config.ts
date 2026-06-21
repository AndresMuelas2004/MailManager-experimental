import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

// Unit-only Vitest config (the `npm run test:unit` CI fast-feedback gate
// required by src/test/CLAUDE.md §7). It mirrors vitest.config.ts but narrows
// `include` to `*.test.ts` only, which excludes the `*.test.tsx` integration
// specs (the ones that render full pages/components). Pure logic and pure hooks
// are authored as `.test.ts`; component/page integration specs are `.test.tsx`.
export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    globals: false,
    css: false,
    include: ['src/**/*.test.ts'],
    exclude: ['node_modules', 'dist', 'e2e'],
  },
});
