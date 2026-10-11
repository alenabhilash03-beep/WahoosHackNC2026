import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, existsSync, rmSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { buildPages } from '../../tools/build-pages.mjs';

test('Pages artifact preserves the existing /web address and excludes credentials and tests', () => {
  const root = mkdtempSync(join(tmpdir(), 'arena-pages-'));
  try {
    const web = join(root, 'web'), out = join(root, 'site');
    mkdirSync(join(web, 'test'), { recursive: true });
    writeFileSync(join(web, 'index.html'), '<script src="config.local.js"></script>');
    writeFileSync(join(web, '.env'), 'SECRET=private');
    writeFileSync(join(web, 'config.local.js'), 'stale-private-config');
    writeFileSync(join(web, 'test', 'private.js'), 'private-test-data');
    buildPages({ webDir: web, outputDir: out, config: {
      SUPABASE_URL: 'https://example.supabase.co', SUPABASE_PUBLISHABLE_KEY: 'sb_publishable_test',
      SUPABASE_SERVICE_ROLE_KEY: 'private-service-role', API_URL: 'https://api.example',
    } });
    assert.ok(existsSync(join(out, 'web', 'index.html')));
    assert.match(readFileSync(join(out, 'index.html'), 'utf8'), /web\/index.html/);
    assert.equal(existsSync(join(out, 'web', '.env')), false);
    assert.equal(existsSync(join(out, 'web', 'test')), false);
    const config = readFileSync(join(out, 'web', 'config.local.js'), 'utf8');
    assert.ok(config.includes('https://api.example'));
    assert.equal(config.includes('private'), false);
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test('Pages build refuses missing config, secret keys and insecure production APIs', () => {
  assert.throws(() => buildPages({ config: {} }));
  assert.throws(() => buildPages({ config: { SUPABASE_URL: 'https://example.supabase.co', SUPABASE_PUBLISHABLE_KEY: 'sb_secret_private' } }));
  assert.throws(() => buildPages({ config: { SUPABASE_URL: 'https://example.supabase.co', SUPABASE_PUBLISHABLE_KEY: 'sb_publishable_test', API_URL: 'http://localhost:8000' } }), /HTTPS/);
});
