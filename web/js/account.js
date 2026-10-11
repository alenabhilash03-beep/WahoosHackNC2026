import {getAuthClient, createAccountAuth, accountSession, loginUrl} from './auth.js';

const nav = document.createElement('nav');
nav.className = 'account-nav'; nav.setAttribute('aria-label', 'Your account');
const label = document.createElement('span'); label.className = 'account-label'; label.setAttribute('role','status');
const login = document.createElement('a'); login.className = 'account-control'; login.href = loginUrl(); login.textContent = 'SIGN IN';
const logout = document.createElement('button'); logout.className = 'account-control'; logout.type = 'button'; logout.textContent = 'SIGN OUT'; logout.hidden = true;
nav.append(label, login, logout); document.body.prepend(nav);

async function initialize() {
  const config = globalThis.ARENA_CONFIG ?? {};
  if (config.LOCAL_MODE || config.DEMO_MODE) { nav.hidden = true; return; }
  try {
    const {auth} = await getAuthClient();
    function render(session) {
      const account = accountSession(session);
      label.textContent = account?.user.email ?? '';
      login.hidden = Boolean(account); logout.hidden = !account;
    }
    let lastUser, signingOut = false;
    const {data:{subscription}} = auth.onAuthStateChange((_event, session) => {
      render(session);
      // Clear private cards/audio when another tab signs out or changes accounts.
      if (_event === 'SIGNED_OUT' && !signingOut && document.body.dataset.arena !== 'home') globalThis.top.location.replace(loginUrl());
      if (_event === 'SIGNED_IN' && document.body.dataset.arena !== 'home') {
        if (lastUser && lastUser !== session?.user.id) location.reload();
        lastUser = session?.user.id;
      }
    });
    window.addEventListener('pagehide', () => subscription.unsubscribe(), {once:true});
    const {data, error} = await auth.getSession();
    if (error) throw error;
    render(data?.session); lastUser = data?.session?.user.id;
    logout.addEventListener('click', async () => {
      logout.disabled = true;
      signingOut = true;
      try { await createAccountAuth().signOut(); globalThis.top.location.replace(new URL('index.html',location.href).href); }
      catch (error) { signingOut = false; label.textContent = error.message || 'Could not sign out. Retry.'; logout.disabled = false; }
    });
  } catch { label.textContent = 'Sign-in unavailable'; }
}
initialize();
// The Back button must not restore a previous user's private report from memory.
window.addEventListener('pageshow', event => { if (event.persisted) location.reload(); });
