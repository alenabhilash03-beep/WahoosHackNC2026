// Hermetic acceptance: real UI, mocked provider redirects, no live users or emails.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '../../web');
const out = path.resolve(__dirname, '../../work/browser-qa');
const sdk = `
export function createClient() {
  const callbacks=[];
  const account={access_token:'test-token',user:{id:'test-user',email:'player@example.com',is_anonymous:false}};
  const session=()=>JSON.parse(localStorage.getItem('test-session')||'null');
  const emit=(event,value)=>callbacks.forEach(callback=>callback(event,value));
  const save=value=>localStorage.setItem('test-session',JSON.stringify(value));
  window.addEventListener('storage',event=>{if(event.key==='test-session')emit(session()?'SIGNED_IN':'SIGNED_OUT',session());});
  return {auth:{
    onAuthStateChange(callback){callbacks.push(callback);queueMicrotask(()=>callback('INITIAL_SESSION',session()));return {data:{subscription:{unsubscribe(){callbacks.splice(callbacks.indexOf(callback),1);}}}};},
    async getSession(){return {data:{session:session()}};},
    async refreshSession(){return {data:{session:session()}};},
    async signInWithOAuth(value){
      localStorage.setItem('test-oauth',JSON.stringify(value));
      if(localStorage.getItem('test-oauth-error'))return {error:{message:'Provider unavailable'}};
      // Change the query too: emulate a full provider round trip, not a same-page hash change.
      save(account); location.assign(value.options.redirectTo+'?test-callback=1#access_token=test-token');
      return {data:{url:'https://provider.example'}};
    },
    async signOut(){save(null);emit('SIGNED_OUT',null);return {error:null};}
  }};
}`;

(async () => {
  fs.mkdirSync(out, {recursive:true});
  const server = http.createServer((request, response) => {
    const pathname = new URL(request.url, 'http://localhost').pathname;
    const file = path.resolve(root, '.' + pathname.replace('/WahoosHackNC2026/', '/'));
    if (!file.startsWith(root + path.sep)) { response.writeHead(404).end(); return; }
    try {
      response.setHeader('Content-Type', ({'.html':'text/html','.js':'application/javascript','.css':'text/css'})[path.extname(file)] || 'application/octet-stream');
      response.end(fs.readFileSync(file));
    } catch { response.writeHead(404).end(); }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const base = `http://127.0.0.1:${server.address().port}/WahoosHackNC2026`;
  let browser;
  try {
    browser = await chromium.launch({headless:true, ...(process.env.BROWSER_EXECUTABLE ? {executablePath:process.env.BROWSER_EXECUTABLE} : {})});
    const context = await browser.newContext({viewport:{width:1280,height:900}});
    const errors = [];
    const page = await context.newPage(); page.on('pageerror', error => errors.push(error.message));
    let settings={google:true,github:true}, settingsFail=false, localMode=false;
    await context.route('**/config.js', route => route.fulfill({contentType:'application/javascript',body:`globalThis.ARENA_CONFIG={SUPABASE_URL:'https://auth.example',SUPABASE_ANON_KEY:'public',API_URL:'',DEMO_MODE:false,LOCAL_MODE:${localMode}};`}));
    await context.route('https://auth.example/auth/v1/settings', route => route.fulfill({status:settingsFail?503:200,contentType:'application/json',body:JSON.stringify({external:settings})}));
    await context.route('https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2/+esm', route => route.fulfill({contentType:'application/javascript',body:sdk}));
    await context.route('**/js/arena.js', route => route.fulfill({contentType:'application/javascript',body:'export const WALK_SECONDS=0; export const showHome=()=>{}; export const walkToStage=async()=>{}; export const cheer=()=>{}; export const pauseRendering=()=>{}; export const startPov=()=>{}; export const walkToPodium=async()=>{};'}));
    await context.route('https://fonts.googleapis.com/**', route => route.fulfill({contentType:'text/css',body:''}));
    await context.route('**/api/me', route => route.fulfill({contentType:'application/json',body:JSON.stringify({id:'test-user',display_name:'player',prompts:[],speeches:[]})}));
    await context.route('**/api/prompts', route => route.fulfill({contentType:'application/json',body:'[]'}));
    await context.route('**/api/leaderboard', route => route.fulfill({contentType:'application/json',body:JSON.stringify({rows:[],you:null,display_name:null})}));
    const google=page.getByRole('button',{name:'Continue with Google',exact:true});
    const github=page.getByRole('button',{name:'Continue with GitHub',exact:true});
    const enabled=()=>google.waitFor({state:'visible'}).then(()=>page.waitForFunction(()=>!document.querySelector('[data-provider="google"]').disabled));

    await page.goto(base+'/progress.html');
    await page.waitForURL('**/login.html?next=progress.html'); await enabled();
    assert.equal(await page.locator('input[type="password"],input[type="email"]').count(),0);
    await page.screenshot({path:path.join(out,'auth-social-desktop.png'),fullPage:true});
    await google.click(); await page.waitForURL('**/progress.html');
    assert.deepEqual(await page.evaluate(()=>JSON.parse(localStorage.getItem('test-oauth'))),{provider:'google',options:{redirectTo:base+'/login.html'}});
    assert.equal(await page.evaluate(()=>sessionStorage.getItem('arena.auth.next')),null);
    await page.getByRole('button',{name:'SIGN OUT',exact:true}).click(); await page.waitForURL('**/index.html');

    await page.goto(base+'/login.html?next=progress.html'); await enabled();
    await page.evaluate(()=>localStorage.setItem('test-oauth-error','true'));
    await github.click();
    await page.locator('#auth-status').filter({hasText:'Provider unavailable'}).waitFor();
    assert.equal(await github.isEnabled(),true);
    await page.goto(base+'/login.html#error=access_denied&error_description=Sensitive+callback+detail');
    await page.locator('#auth-status').filter({hasText:'cancelled'}).waitFor();
    assert.equal(new URL(page.url()).hash,'');
    assert.equal((await page.locator('body').innerText()).includes('Sensitive callback detail'),false);
    await page.evaluate(()=>localStorage.removeItem('test-oauth-error'));
    await github.click(); await page.waitForURL('**/progress.html');
    const second=await context.newPage(); await second.goto(base+'/index.html');
    await second.getByRole('button',{name:'SIGN OUT',exact:true}).click();
    await page.waitForURL('**/login.html?next=progress.html'); await second.close();

    await page.goto(base+'/leaderboard.html');
    await page.waitForURL('**/login.html?next=leaderboard.html'); await enabled();
    await google.click(); await page.waitForURL('**/leaderboard.html');
    await page.getByRole('button',{name:'SIGN OUT',exact:true}).click(); await page.waitForURL('**/index.html');

    await page.goto(base+'/login.html?next=https://evil.example'); await enabled();
    await github.click(); await page.waitForURL('**/practice.html');
    await page.getByRole('button',{name:'SIGN OUT',exact:true}).click(); await page.waitForURL('**/index.html');
    await page.evaluate(()=>{sessionStorage.setItem('arena.auth.next','https://evil.example');localStorage.setItem('test-session',JSON.stringify({access_token:'test-token',user:{is_anonymous:false,email:'player@example.com'}}));});
    await page.goto(base+'/login.html'); await page.waitForURL('**/practice.html');
    await page.getByRole('button',{name:'SIGN OUT',exact:true}).click(); await page.waitForURL('**/index.html');

    settings={google:false,github:true}; await page.goto(base+'/login.html');
    await page.locator('#auth-status').filter({hasText:'Google sign-in is being set up'}).waitFor();
    assert.equal(await google.isEnabled(),false); assert.equal(await github.isEnabled(),true);
    settings={google:false,github:false}; await page.reload();
    await page.locator('#auth-status').filter({hasText:'Sign-in is being set up'}).waitFor();
    assert.equal(await github.isEnabled(),false);
    settingsFail=true; await page.reload();
    await page.locator('#auth-status').filter({hasText:'Could not load sign-in options'}).waitFor();
    assert.equal(await google.isEnabled(),false);
    settingsFail=false; settings={google:true,github:true};
    await page.getByRole('button',{name:'Try again',exact:true}).click(); await enabled();
    await page.setViewportSize({width:390,height:844});
    await page.screenshot({path:path.join(out,'auth-social-mobile.png'),fullPage:true});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);

    await page.evaluate(()=>localStorage.setItem('test-session',JSON.stringify({access_token:'guest',user:{is_anonymous:true}})));
    await page.goto(base+'/progress.html'); await page.waitForURL('**/login.html?next=progress.html');
    localMode=true; await page.reload(); await page.locator('#local-play').waitFor({state:'visible'});
    assert.equal(await google.isEnabled(),false);
    assert.deepEqual(errors,[]);
    console.log('PASS: Google/GitHub callbacks, private return paths, provider failures, cancellation, disabled providers, retry, logout, cross-tab logout, unsafe redirects, guest rejection, mobile layout, local mode; no page errors.');
  } finally {
    if(browser) await browser.close();
    await new Promise(resolve=>server.close(resolve));
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
