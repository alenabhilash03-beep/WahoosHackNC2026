import { writeFileSync } from 'node:fs';
import { validateConfig } from '../web/js/auth-service.mjs';

// Node 22+: local .env is optional when deployment supplies environment variables.
try { process.loadEnvFile(new URL('../.env', import.meta.url)); }
catch (error) { if (error.code !== 'ENOENT') throw error; }
const config = validateConfig({
  url: process.env.SUPABASE_URL,
  key: process.env.SUPABASE_PUBLISHABLE_KEY || process.env.SUPABASE_ANON_KEY,
});
const publicConfig = {
  API_URL: process.env.API_URL || '', SUPABASE_URL: config.url,
  SUPABASE_PUBLISHABLE_KEY: config.key, DEMO_MODE: false,
};
writeFileSync(new URL('../web/config.local.js', import.meta.url),
  `// Generated public browser configuration. Never include server secrets.\nwindow.ARENA_CONFIG = ${JSON.stringify(publicConfig)};\n`);
console.log('Generated web/config.local.js with public API and Supabase configuration.');
