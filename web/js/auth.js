import { createAuthService, validateConfig } from './auth-service.mjs';

const dialog = document.querySelector('#account-dialog');
const form = document.querySelector('#account-form');
const message = document.querySelector('#account-message');
const account = document.querySelector('#account-open');
const logout = document.querySelector('#account-logout');
const switchMode = document.querySelector('#account-switch');
const submit = document.querySelector('#account-submit');
let mode = 'login';
let session = null;
let service;
let busy = false;
let ready = false;

function showMessage(text) { message.textContent = text; }
function setMode(next) {
  mode = next;
  const signup = mode === 'signup';
  document.querySelector('#account-heading').textContent = signup ? 'CREATE ACCOUNT' : 'LOG IN';
  document.querySelector('#name-field').hidden = !signup;
  form.elements.displayName.required = signup;
  form.elements.password.autocomplete = signup ? 'new-password' : 'current-password';
  form.elements.password.minLength = signup ? 8 : 1;
  form.elements.password.value = '';
  submit.textContent = signup ? 'CREATE ACCOUNT' : 'LOG IN';
  switchMode.textContent = signup ? 'Already registered? Log in' : 'New here? Create an account';
  showMessage('');
}
function renderSession(next) {
  session = next;
  document.querySelector('#account-user').textContent = next
    ? `Welcome, ${next.user.user_metadata?.display_name || next.user.email || 'player'}` : '';
  account.hidden = !!next;
  logout.hidden = !next;
  window.dispatchEvent(new CustomEvent('arena:auth', { detail: { user: next?.user ?? null } }));
}
account.addEventListener('click', () => dialog.showModal());
document.querySelector('#account-close').addEventListener('click', () => dialog.close());
dialog.addEventListener('close', () => { form.elements.password.value = ''; });
switchMode.addEventListener('click', () => setMode(mode === 'login' ? 'signup' : 'login'));
form.addEventListener('submit', async (event) => {
  event.preventDefault();
  if (busy || !service) return;
  busy = true;
  submit.disabled = switchMode.disabled = true;
  showMessage('Please wait…');
  try {
    const data = await service.submit(mode, {
      email: form.elements.email.value, password: form.elements.password.value,
      displayName: form.elements.displayName.value,
    });
    form.elements.password.value = '';
    if (data.session) { renderSession(data.session); dialog.close(); }
    else showMessage('Check your email for a confirmation link, then log in. If already registered, try logging in.');
  } catch (error) { showMessage(error.message || 'Unable to connect. Please try again.'); }
  finally { busy = false; submit.disabled = switchMode.disabled = false; }
});
logout.addEventListener('click', async () => {
  logout.disabled = true;
  try { await service.signOut(); renderSession(null); }
  catch (error) { dialog.showModal(); showMessage(error.message || 'Logout failed. Please try again.'); }
  finally { logout.disabled = false; }
});
// Capture before the existing scene handler. This is a UI gate; future API endpoints
// must independently verify the user's JWT, never trust this browser state.
document.querySelector('#argue').addEventListener('click', (event) => {
  if (!ready || !session) {
    event.stopImmediatePropagation();
    dialog.showModal();
    if (ready) showMessage('Log in to enter the arena.');
  }
}, true);
setMode('login');
submit.disabled = true;
try {
  const config = validateConfig(window.ARENA_CONFIG);
  const { createClient } = await import('https://esm.sh/@supabase/supabase-js@2.49.8');
  const client = createClient(config.url, config.key);
  service = createAuthService(client, new URL('./index.html', location.href).href);
  // INITIAL_SESSION restores storage/confirmation sessions without racing getSession.
  client.auth.onAuthStateChange((_event, next) => { renderSession(next); ready = true; });
  submit.disabled = false;
  const callbackError = new URLSearchParams(location.hash.slice(1)).get('error_description');
  if (callbackError) { dialog.showModal(); showMessage(callbackError); history.replaceState(null, '', location.pathname); }
} catch {
  showMessage('Accounts are unavailable. Ask the site owner to configure Supabase and reload.');
  switchMode.disabled = true;
}
