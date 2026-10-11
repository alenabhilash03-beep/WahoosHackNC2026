// One client per page: login controls and API requests share the same session.
let clientPromise;
export function getAuthClient() {
  if (!clientPromise) {
    const config = globalThis.ARENA_CONFIG ?? {};
    if (!config.SUPABASE_URL || !config.SUPABASE_ANON_KEY) {
      throw new Error('Sign-in is not configured. Set the public Supabase values in config.js.');
    }
    clientPromise = import('https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2/+esm')
      .then(({createClient}) => createClient(config.SUPABASE_URL, config.SUPABASE_ANON_KEY))
      .catch(error => { clientPromise = undefined; throw error; });
  }
  return clientPromise;
}

export function accountSession(session) {
  return session?.access_token && session.user && !session.user.is_anonymous ? session : null;
}

export class AuthRequiredError extends Error {
  constructor() { super('Sign in to continue.'); this.status = 401; }
}

const PAGES = new Set(['index.html', 'practice.html', 'progress.html', 'card.html']);
export function safeNext(value, href = globalThis.location.href) {
  try {
    const base = new URL('.', href), target = new URL(value || 'practice.html', base);
    const name = target.pathname.slice(base.pathname.length);
    if (target.origin === base.origin && target.pathname.startsWith(base.pathname) &&
        !target.username && !target.password && PAGES.has(name)) return name + target.search;
  } catch { /* Invalid or external return links fall back to practice. */ }
  return 'practice.html';
}

export function loginUrl(href = globalThis.location.href) {
  const current = new URL(href), url = new URL('login.html', current);
  const page = current.pathname.split('/').pop() || 'index.html';
  url.searchParams.set('next', safeNext(page + current.search, href));
  return url.href;
}

export function createAccountAuth({loadClient = getAuthClient, href = globalThis.location?.href} = {}) {
  const callback = new URL('login.html', href);
  async function call(method, ...args) {
    const {auth} = await loadClient();
    const {data, error} = await auth[method](...args);
    if (error) throw new Error(error.message);
    return data;
  }
  return {
    signUp: (email, password) => call('signUp', {
      email: email.trim(), password, options: {emailRedirectTo: callback.href}
    }),
    signIn: (email, password) => call('signInWithPassword', {email: email.trim(), password}),
    resetPassword: email => {
      const recovery = new URL(callback); recovery.searchParams.set('mode', 'recovery');
      return call('resetPasswordForEmail', email.trim(), {redirectTo: recovery.href});
    },
    updatePassword: password => call('updateUser', {password}),
    signOut: () => call('signOut', {scope: 'local'}),
  };
}

export function redirectToLogin(href = globalThis.location.href) {
  // Speech cards can be embedded inside the practice page.
  const target = globalThis.top ?? globalThis;
  target.location.replace(loginUrl(href));
}

export async function requireAccount(next) {
  const config = globalThis.ARENA_CONFIG ?? {};
  if (config.DEMO_MODE || config.LOCAL_MODE) return true;
  const {auth} = await getAuthClient();
  const {data, error} = await auth.getSession();
  if (error) throw new Error(error.message);
  if (!accountSession(data?.session)) {
    redirectToLogin(next ? new URL(safeNext(next), globalThis.location.href).href : globalThis.location.href);
    return false;
  }
  return true;
}
