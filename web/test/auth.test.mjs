import test from 'node:test';
import assert from 'node:assert/strict';
import { createAuthService, validateConfig } from '../js/auth-service.mjs';

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
