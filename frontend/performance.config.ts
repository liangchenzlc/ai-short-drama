import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './performance', workers: 1, timeout: 45000,
  outputDir: '.runtime/performance-artifacts', reporter: [['list']],
  use: {
    baseURL: process.env.PLAYWRIGHT_PRODUCTION_URL ?? 'http://127.0.0.1:4180',
    viewport: { width: 1440, height: 1000 },
    launchOptions: { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH },
  },
});
