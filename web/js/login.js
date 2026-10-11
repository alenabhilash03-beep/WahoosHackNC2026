import {getAuthClient, getLoginProviders, createAccountAuth, accountSession, safeNext} from './auth.js';

const $ = id => document.getElementById(id);
const params = new URLSearchParams(location.search);
const fragment = new URLSearchParams(location.hash.slice(1));
const returnKey = 'arena.auth.next';
const buttons = [...document.querySelectorAll('[data-provider]')];
let storedNext;
try { storedNext = sessionStorage.getItem(returnKey); } catch { /* Default to practice when storage is blocked. */ }
const destination = safeNext(params.has('next') ? params.get('next') : storedNext);
let callbackFailed = params.has('error') || params.has('error_description') || fragment.has('error') || fragment.has('error_description');
let ready = false, busy = false, leaving = false, enabledProviders = [], service, subscription;

function status(message, error = false) {
  $('auth-status').textContent = message;
  $('auth-status').dataset.error = String(error);
}
function controls() {
  $('auth-providers').setAttribute('aria-busy', String(!ready || busy));
  for (const button of buttons) button.disabled = !ready || busy || !enabledProviders.includes(button.dataset.provider);
  $('auth-retry').disabled = busy;
}
function finishSignIn(session) {
  if (leaving || callbackFailed || !accountSession(session)) return false;
  leaving = true;
  try { sessionStorage.removeItem(returnKey); } catch { /* The safe destination is already resolved. */ }
  // Remove callback tokens and provider details before leaving the page.
  history.replaceState(null, '', location.pathname);
  location.replace(destination);
  return true;
}
for (const button of buttons) button.addEventListener('click', async () => {
  if (!ready || busy || !enabledProviders.includes(button.dataset.provider)) return;
  busy = true; controls(); status(`Opening ${button.dataset.provider === 'google' ? 'Google' : 'GitHub'}…`);
  try {
    // Keep the allow-listed callback exact; preserve only a validated app path per tab.
    sessionStorage.setItem(returnKey, destination);
    await service.signInWithProvider(button.dataset.provider);
  } catch (error) {
    busy = false; controls(); status(error.message || 'Could not sign in. Please retry.', true);
  }
});
$('auth-retry').addEventListener('click', initialize);
window.addEventListener('pagehide', () => subscription?.unsubscribe());
window.addEventListener('pageshow', event => { if (event.persisted) location.reload(); });

async function initialize() {
  ready = false; busy = false; enabledProviders = []; controls();
  $('auth-retry').hidden = true; status('Connecting…');
  const config = globalThis.ARENA_CONFIG ?? {};
  if (config.LOCAL_MODE || config.DEMO_MODE) {
    status('You’re using local practice. Accounts are available on the online arena.');
    $('local-play').hidden = false; return;
  }
  try {
    const {auth} = await getAuthClient();
    service = createAccountAuth();
    subscription?.unsubscribe();
    // Stay synchronous: Supabase auth methods share a lock with this callback.
    ({data: {subscription}} = auth.onAuthStateChange((_event, session) => finishSignIn(session)));
    const {data, error} = await auth.getSession();
    if (error && !callbackFailed) throw error;
    const cancelled = callbackFailed;
    if (cancelled) {
      history.replaceState(null, '', location.pathname);
      callbackFailed = false;
    } else if (finishSignIn(data?.session)) return;
    if (leaving) return;
    enabledProviders = await getLoginProviders();
    ready = true; controls();
    if (cancelled) status('Sign-in was cancelled or could not be completed. Try again.', true);
    else if (!enabledProviders.length) {
      status('Sign-in is being set up. Please check back soon.'); $('auth-retry').hidden = false;
    } else if (enabledProviders.length === 1) {
      const missing = enabledProviders[0] === 'google' ? 'GitHub' : 'Google';
      status(`${missing} sign-in is being set up. You can use the other option.`);
    } else status('');
  } catch (error) {
    status(error.message || 'Could not load sign-in options. Please retry.', true);
    $('auth-retry').hidden = false;
  }
}
initialize();
