import { mkdirSync, readdirSync, readFileSync, writeFileSync, existsSync } from 'node:fs';
import { resolve, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { validateConfig } from '../web/js/auth-service.mjs';

export function buildPages({ webDir, outputDir, config }) {
  const { url, key } = validateConfig(config);
  const api = config.API_URL || '';
  if (api) {
    const parsed = new URL(api);
    if (parsed.protocol !== 'https:' || parsed.username || parsed.password || parsed.search || parsed.hash) {
      throw new Error('Production API_URL must be an HTTPS backend URL without credentials.');
    }
  }
  if (existsSync(outputDir) && readdirSync(outputDir).length) throw new Error('Use an empty output directory for the Pages artifact.');
  const publicConfig = { API_URL: api, SUPABASE_URL: url, SUPABASE_PUBLISHABLE_KEY: key, DEMO_MODE: false };
  mkdirSync(join(outputDir, 'web'), { recursive: true });
  // Publish only browser assets, never repository contents, test files or env files.
  function copy(from, to) {
    for (const entry of readdirSync(from, { withFileTypes: true })) {
      if (entry.name.startsWith('.') || ['test', 'node_modules', 'config.local.js', 'package.json'].includes(entry.name)) continue;
      if (entry.isDirectory()) { mkdirSync(join(to, entry.name), { recursive: true }); copy(join(from, entry.name), join(to, entry.name)); }
      else if (entry.isFile() && /\.(html|css|js|mjs|png|jpg|jpeg|svg|webp|ico|woff2?)$/i.test(entry.name)) {
        writeFileSync(join(to, entry.name), readFileSync(join(from, entry.name)));
      }
    }
  }
  copy(webDir, join(outputDir, 'web'));
  writeFileSync(join(outputDir, 'web', 'config.local.js'), `window.ARENA_CONFIG = ${JSON.stringify(publicConfig)};\n`);
  writeFileSync(join(outputDir, '.nojekyll'), '');
  writeFileSync(join(outputDir, 'index.html'), '<!doctype html><html lang="en"><meta charset="utf-8"><meta http-equiv="refresh" content="0;url=web/index.html"><title>Debate Arena</title><a href="web/index.html">Open Debate Arena</a></html>\n');
  return publicConfig;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const root = fileURLToPath(new URL('../', import.meta.url));
  try {
    buildPages({ webDir: join(root, 'web'), outputDir: join(root, 'work', 'site'), config: {
      SUPABASE_URL: process.env.SUPABASE_URL,
      SUPABASE_PUBLISHABLE_KEY: process.env.SUPABASE_PUBLISHABLE_KEY,
      API_URL: process.env.API_URL,
    } });
    console.log('Built work/site, preserving /web/index.html. Only public browser settings are included.');
  } catch {
    console.error('Pages build failed. Set repository Actions variables SUPABASE_URL and SUPABASE_PUBLISHABLE_KEY to real public project settings; API_URL, if supplied, must use HTTPS. Use an empty work/site directory.');
    process.exitCode = 1;
  }
}
