export function validateConfig(config = {}) {
  const url = config.SUPABASE_URL ?? config.url;
  const key = config.SUPABASE_PUBLISHABLE_KEY || config.SUPABASE_ANON_KEY || config.key;
  let parsed;
  try { parsed = new URL(url); } catch { throw new Error('Set a valid SUPABASE_URL.'); }
  if (parsed.protocol !== 'https:' || parsed.username || parsed.password || parsed.search || parsed.hash || parsed.pathname !== '/') {
    throw new Error('SUPABASE_URL must be an HTTPS project origin.');
  }
  let publicKey = typeof key === 'string' && key.startsWith('sb_publishable_') && !key.includes('REPLACE_ME');
  // Legacy anon JWTs remain supported by the existing backend deployment.
  // Decode only to reject privileged configuration; this does not verify identity.
  if (typeof key === 'string' && key.split('.').length === 3) {
    try {
      const payload = key.split('.')[1].replace(/-/g, '+').replace(/_/g, '/');
      publicKey = JSON.parse(atob(payload)).role === 'anon';
    } catch { publicKey = false; }
  }
  if (!publicKey) {
    throw new Error('Set SUPABASE_PUBLISHABLE_KEY to a publishable key, never a secret or service-role key.');
  }
  if (parsed.hostname.includes('your_project')) throw new Error('Replace the example project URL.');
  return { url: parsed.origin, key };
}

export function gateArenaLink(event, { ready, session, demoMode, hostname }) {
  const localDemo = demoMode && ['localhost', '127.0.0.1', '[::1]', '::1'].includes(hostname);
  if (localDemo || (ready && session && !session.user?.is_anonymous)) return false;
  event.preventDefault();
  event.stopImmediatePropagation();
  return true;
}

export function createAuthService(client, redirectUrl) {
  return {
    async submit(mode, { email, password, displayName }) {
      email = email.trim();
      if (!email || !password) throw new Error('Enter your email and password.');
      let result;
      if (mode === 'signup') {
        displayName = displayName.trim();
        if (!displayName || displayName.length > 40) throw new Error('Use a display name of 1–40 characters.');
        if (password.length < 8) throw new Error('Use a password with at least 8 characters.');
        result = await client.auth.signUp({ email, password, options: {
          data: { display_name: displayName }, emailRedirectTo: redirectUrl,
        } });
      } else if (mode === 'login') {
        result = await client.auth.signInWithPassword({ email, password });
      } else throw new Error('Unknown account action.');
      if (result.error) throw result.error;
      return result.data;
    },
    async signOut() {
      const { error } = await client.auth.signOut({ scope: 'local' });
      if (error) throw error;
    },
  };
}
