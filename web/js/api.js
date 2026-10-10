// Public config only. Supabase refreshes its stored session; the server resolves
// the bearer token and never accepts a player id from this client.
export function createApiClient({config = {}, fetchImpl = globalThis.fetch,
  loadAuth, hostname = globalThis.location?.hostname ?? '', timeoutMs = 60000} = {}) {
  let clientPromise, sessionPromise;
  const client = () => clientPromise ??= (loadAuth ? loadAuth() :
    import('https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2/+esm')
      .then(({createClient}) => createClient(config.SUPABASE_URL, config.SUPABASE_ANON_KEY)));
  async function token(refresh = false) {
    if (config.DEMO_MODE) {
      if (!['localhost','127.0.0.1','[::1]','::1'].includes(hostname)) throw new Error('Demo mode is local only.');
      return 'local-demo';
    }
    if (!config.SUPABASE_URL || !config.SUPABASE_ANON_KEY) throw new Error('Sign-in is not configured. Set the public Supabase values and API_URL in config.js.');
    if (!sessionPromise) sessionPromise = (async () => {
      const {auth} = await client();
      let result = refresh ? await auth.refreshSession() : await auth.getSession();
      if (result.error) throw new Error(result.error.message);
      if (!result.data?.session) result = await auth.signInAnonymously();
      if (result.error) throw new Error(result.error.message);
      const value = result.data?.session?.access_token;
      if (!value) throw new Error('Sign-in returned no session. Enable anonymous sign-ins in Supabase.');
      return value;
    })();
    try { return await sessionPromise; } finally { sessionPromise = null; }
  }
  return async function request(path, options = {}) {
    if (!path.startsWith('/api/')) throw new Error('API paths must start with /api/.');
    const send = async (refresh = false) => {
      const headers = new Headers(options.headers);
      headers.set('Authorization', `Bearer ${await token(refresh)}`);
      if (config.DEMO_MODE) headers.set('X-Demo-Mode','true');
      let body = options.body;
      if (body && typeof body === 'object' && !(body instanceof FormData) && !(body instanceof Blob)) {
        headers.set('Content-Type','application/json'); body = JSON.stringify(body);
      }
      const controller = new AbortController();
      const timeout = setTimeout(()=>controller.abort(),timeoutMs);
      const signal = options.signal ? AbortSignal.any([options.signal,controller.signal]) : controller.signal;
      let response;
      try {
        response = await fetchImpl(`${(config.API_URL ?? '').replace(/\/$/,'')}${path}`,{...options,body,headers,signal});
        if (response.status === 401 && !refresh && !config.DEMO_MODE) return await send(true);
        const raw = await response.text();
        let data;
        try { data = JSON.parse(raw); } catch { throw new Error(`The server returned HTTP ${response.status} without JSON. Check API_URL and the server connection.`); }
        if (!response.ok) {
          const detail = typeof data.detail === 'string' ? data.detail : `Request failed (HTTP ${response.status}).`;
          const error = new Error(detail); error.status = response.status; throw error;
        }
        return data;
      } catch (error) {
        if (signal.aborted) throw new Error('The request was interrupted or took too long. Please retry.');
        if (error instanceof TypeError) throw new Error('Could not reach the server. Check your connection and API_URL.');
        throw error;
      } finally { clearTimeout(timeout); }
    };
    return send();
  };
}

let defaultClient;
export function api(path,options) {
  defaultClient ??= createApiClient({config:globalThis.ARENA_CONFIG ?? {}});
  return defaultClient(path,options);
}
