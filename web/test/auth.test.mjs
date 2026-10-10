import test from 'node:test';
import assert from 'node:assert/strict';
import { createAuthService, validateConfig, gateArenaLink } from '../js/auth-service.mjs';
import { createApiClient } from '../js/api.js';
import { readFileSync, mkdtempSync, mkdirSync, cpSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { execFileSync } from 'node:child_process';
import vm from 'node:vm';

test('only public keys and HTTPS origins can reach browser config', () => {
  assert.deepEqual(validateConfig({ url: 'https://example.supabase.co', key: 'sb_publishable_test' }),
    { url: 'https://example.supabase.co', key: 'sb_publishable_test' });
  for (const key of ['', 'sb_secret_private', 'eyJserviceRole', 'sb_publishable_REPLACE_ME']) {
    assert.throws(() => validateConfig({ url: 'https://example.supabase.co', key }));
  }
  for (const url of ['invalid', 'http://example.com', 'https://user:password@example.com', 'https://example.com/path']) {
    assert.throws(() => validateConfig({ url, key: 'sb_publishable_test' }));
  }
});

test('account configuration accepts backend field names, publishable and legacy anon keys', () => {
  const key = 'sb_publishable_test';
  assert.equal(validateConfig({ SUPABASE_URL: 'https://example.supabase.co', SUPABASE_PUBLISHABLE_KEY: key }).key, key);
  const jwt = role => `header.${Buffer.from(JSON.stringify({ role })).toString('base64url')}.signature`;
  assert.equal(validateConfig({ SUPABASE_URL: 'https://example.supabase.co', SUPABASE_ANON_KEY: jwt('anon') }).key, jwt('anon'));
  assert.throws(() => validateConfig({ SUPABASE_URL: 'https://example.supabase.co', SUPABASE_ANON_KEY: jwt('service_role') }));
});

test('home gate cancels signed-out navigation and permits accounts and local demo', () => {
  for (const options of [
    { ready: false, session: null }, { ready: true, session: null },
    { ready: true, session: { user: { is_anonymous: true } } },
    { demoMode: true, hostname: 'example.com' },
  ]) {
    let prevented = false, stopped = false;
    assert.equal(gateArenaLink({ preventDefault() { prevented = true; }, stopImmediatePropagation() { stopped = true; } }, options), true);
    assert.equal(prevented && stopped, true);
  }
  const event = { preventDefault() { assert.fail('must navigate'); }, stopImmediatePropagation() { assert.fail('must navigate'); } };
  assert.equal(gateArenaLink(event, { ready: true, session: { user: { id: 'account' } } }), false);
  assert.equal(gateArenaLink(event, { demoMode: true, hostname: 'localhost' }), false);
});

test('generated settings work with both account validation and the recording/progress API', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'arena-auth-'));
  try {
    mkdirSync(join(dir, 'tools')); mkdirSync(join(dir, 'web', 'js'), { recursive: true });
    cpSync(new URL('../../tools/configure-web.mjs', import.meta.url), join(dir, 'tools', 'configure-web.mjs'));
    cpSync(new URL('../js/auth-service.mjs', import.meta.url), join(dir, 'web', 'js', 'auth-service.mjs'));
    execFileSync(process.execPath, [join(dir, 'tools', 'configure-web.mjs')], { env: {
      ...process.env, SUPABASE_URL: 'https://example.supabase.co', SUPABASE_PUBLISHABLE_KEY: 'sb_publishable_test',
      SUPABASE_SERVICE_ROLE_KEY: 'must-not-be-exposed', API_URL: 'https://api.example',
    } });
    const source = readFileSync(join(dir, 'web', 'config.local.js'), 'utf8');
    assert.equal(source.includes('must-not-be-exposed'), false);
    const context = { window: {} }; vm.runInNewContext(source, context);
    const config = context.window.ARENA_CONFIG;
    assert.equal(validateConfig(config).key, 'sb_publishable_test');
    const request = createApiClient({ config,
      loadAuth: async () => ({ auth: { getSession: async () => ({ data: { session: { access_token: 'account-token' } } }) } }),
      fetchImpl: async (url, options) => {
        assert.equal(url, 'https://api.example/api/me');
        assert.equal(options.headers.get('Authorization'), 'Bearer account-token');
        return new Response('{}');
      },
    });
    await request('/api/me');
    for (const page of ['index', 'practice', 'card', 'progress']) {
      const html = readFileSync(new URL(`../${page}.html`, import.meta.url), 'utf8');
      assert.ok(html.indexOf('src="config.js"') < html.indexOf('src="config.local.js"'));
    }
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

test('signup passes metadata and a confirmation redirect, allows pending confirmation', async () => {
  let received;
  const service = createAuthService({ auth: { signUp: async args => {
    received = args; return { data: { session: null }, error: null };
  } } }, 'https://example.com/repo/index.html');
  assert.equal((await service.submit('signup', {
    email: ' player@example.com ', password: 'long-password', displayName: ' Player ',
  })).session, null);
  assert.deepEqual(received, { email: 'player@example.com', password: 'long-password', options: {
    data: { display_name: 'Player' }, emailRedirectTo: 'https://example.com/repo/index.html',
  } });
});

test('login returns session and does not send signup metadata', async () => {
  const session = { user: { id: 'player' } };
  const service = createAuthService({ auth: { signInWithPassword: async args => {
    assert.deepEqual(args, { email: 'p@example.com', password: 'existing' });
    return { data: { session }, error: null };
  } } });
  assert.equal((await service.submit('login', { email: 'p@example.com', password: 'existing' })).session, session);
});

test('invalid signup is rejected before contacting Auth', async () => {
  const service = createAuthService({ auth: { signUp: () => assert.fail('must not send') } });
  await assert.rejects(service.submit('signup', { email: 'p@example.com', password: 'short', displayName: 'Player' }), /8 characters/);
  await assert.rejects(service.submit('signup', { email: 'p@example.com', password: 'long-password', displayName: ' ' }), /display name/);
});

test('Auth errors reach UI and logout uses local scope', async () => {
  const service = createAuthService({ auth: {
    signInWithPassword: async () => ({ error: new Error('Invalid credentials') }),
    signOut: async args => { assert.equal(args.scope, 'local'); return { error: new Error('Offline') }; },
  } });
  await assert.rejects(service.submit('login', { email: 'p@example.com', password: 'wrong' }), /Invalid credentials/);
  await assert.rejects(service.signOut(), /Offline/);
});
