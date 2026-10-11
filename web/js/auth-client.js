import { validateConfig } from './auth-service.mjs';

let clientPromise;
// Home, practice, cards and progress share one SDK version and storage behavior.
export function getAuthClient(config = globalThis.ARENA_CONFIG ?? {}) {
  if (!clientPromise) {
    const { url, key } = validateConfig(config);
    clientPromise = import('https://esm.sh/@supabase/supabase-js@2.117.3')
      .then(({ createClient }) => createClient(url, key))
      .catch(error => { clientPromise = undefined; throw error; });
  }
  return clientPromise;
}
