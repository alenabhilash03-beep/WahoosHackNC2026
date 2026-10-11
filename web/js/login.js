import {getAuthClient, createAccountAuth, accountSession, safeNext} from './auth.js';

const $ = id => document.getElementById(id);
const params = new URLSearchParams(location.search);
const fragment = new URLSearchParams(location.hash.slice(1));
const destination = safeNext(params.get('next'));
let mode = params.get('mode') === 'recovery' || fragment.get('type') === 'recovery' ? 'recovery' : 'signin';
let ready = false, busy = false, auth, service;
const labels = {signin:'SIGN IN', signup:'CREATE ACCOUNT', reset:'SEND RESET LINK', recovery:'SAVE NEW PASSWORD'};

function status(message, error = false) {
  $('auth-status').textContent = message;
  $('auth-status').dataset.error = String(error);
}
function controls() {
  $('auth-fields').disabled = !ready || busy;
  $('auth-form').setAttribute('aria-busy', String(!ready || busy));
  for (const button of document.querySelectorAll('[data-mode], #forgot-password')) button.disabled = busy;
}
function showMode(next) {
  mode = next;
  $('auth-title').textContent = labels[mode];
  $('auth-submit').textContent = labels[mode];
  $('email-field').hidden = mode === 'recovery';
  $('email').required = mode !== 'recovery';
  $('password-field').hidden = mode === 'reset';
  $('password').required = mode !== 'reset';
  $('password').minLength = ['signup','recovery'].includes(mode) ? 8 : 1;
  $('password').autocomplete = mode === 'signin' ? 'current-password' : 'new-password';
  $('password').value = ''; $('confirm-password').value = '';
  $('confirm-field').hidden = !['signup','recovery'].includes(mode);
  $('confirm-password').required = ['signup','recovery'].includes(mode);
  $('password-help').hidden = !['signup','recovery'].includes(mode);
  $('forgot-password').hidden = mode !== 'signin';
  $('auth-copy').textContent = mode === 'reset' ? 'We’ll email you a link to choose a new password.' : mode === 'recovery' ? 'Choose a new password for your account.' : 'Keep your speeches and progress with you, wherever you sign in.';
  for (const button of document.querySelectorAll('[data-mode]')) button.setAttribute('aria-pressed', String(button.dataset.mode === mode));
  controls();
}
for (const button of document.querySelectorAll('[data-mode]')) button.addEventListener('click', () => {
  showMode(button.dataset.mode); status(''); $('email').focus();
});
$('forgot-password').addEventListener('click', () => { showMode('reset'); status(''); $('email').focus(); });
$('auth-form').addEventListener('submit', async event => {
  event.preventDefault();
  if (!ready || busy) return;
  if (['signup','recovery'].includes(mode) && $('password').value !== $('confirm-password').value) {
    status('Passwords do not match.', true); $('confirm-password').focus(); return;
  }
  busy = true; controls(); status('Please wait…');
  try {
    if (mode === 'reset') {
      await service.resetPassword($('email').value);
      status('If an account exists for that email, a reset link is on its way. Check your inbox.');
    } else if (mode === 'recovery') {
      const {data, error} = await auth.getSession();
      if (error || !accountSession(data?.session)) throw new Error('This reset link has expired. Request a new one.');
      await service.updatePassword($('password').value);
      $('password').value = ''; $('confirm-password').value = '';
      location.replace(destination);
    } else {
      const data = await service[mode === 'signup' ? 'signUp' : 'signIn']($('email').value, $('password').value);
      $('password').value = ''; $('confirm-password').value = '';
      if (accountSession(data?.session)) location.replace(destination);
      else status('Check your email for the confirmation link, then return here to sign in.');
    }
  } catch (error) { status(error.message || 'Could not connect. Please retry.', true); }
  finally { busy = false; controls(); }
});

async function initialize() {
  showMode(mode);
  const config = globalThis.ARENA_CONFIG ?? {};
  if (config.LOCAL_MODE || config.DEMO_MODE) {
    status('You’re using local practice. Accounts are available on the online arena.');
    $('local-play').hidden = false; return;
  }
  try {
    ({auth} = await getAuthClient());
    service = createAccountAuth();
    // Keep this callback synchronous: Supabase auth methods acquire the same lock.
    const {data:{subscription}} = auth.onAuthStateChange(event => {
      if (event === 'PASSWORD_RECOVERY') showMode('recovery');
    });
    window.addEventListener('pagehide', () => subscription.unsubscribe(), {once:true});
    const {data, error} = await auth.getSession();
    if (error) throw error;
    if (fragment.get('error_description') || params.get('error_description')) {
      // Clear callback tokens and error details from the address bar.
      history.replaceState(null, '', location.pathname + location.search);
      showMode('reset'); status('This email link is invalid or expired. Request a new link.', true);
    } else if (mode === 'recovery') {
      if (!accountSession(data?.session)) { showMode('reset'); status('This reset link has expired. Request a new one.', true); }
      else status('Your reset link is verified. Enter your new password.');
    } else if (accountSession(data?.session)) {
      location.replace(destination); return;
    } else status('');
    ready = true; controls();
  } catch (error) { status(error.message || 'Could not connect. Reload to retry.', true); }
}
initialize();
