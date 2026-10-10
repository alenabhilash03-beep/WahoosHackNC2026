import { writeFileSync } from 'node:fs';
import { validateConfig } from '../web/js/auth-service.mjs';

// Node 22+: local .env is optional when deployment supplies environment variables.
try { process.loadEnvFile(new URL('../.env', import.meta.url)); }
catch (error) { if (error.code !== 'ENOENT') throw error; }
const config = validateConfig({
  url: process.env.SUPABASE_URL,
  key: process.env.SUPABASE_PUBLISHABLE_KEY,
});
writeFileSync(new URL('../web/config.js', import.meta.url),
  `// Generated public browser configuration. Never include server secrets.\nwindow.ARENA_CONFIG = ${JSON.stringify(config)};\n`);
console.log('Generated web/config.js with public Supabase configuration.');
