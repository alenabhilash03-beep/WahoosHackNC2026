// Hermetic browser acceptance: real UI, mocked Supabase, no email or live users.
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
  if(new URLSearchParams(location.search).get('mode')==='recovery' && !location.search.includes('expired=1'))save(account);
  return {auth:{
    onAuthStateChange(callback){callbacks.push(callback);queueMicrotask(()=>callback('INITIAL_SESSION',session()));return {data:{subscription:{unsubscribe(){callbacks.splice(callbacks.indexOf(callback),1);}}}};},
    async getSession(){return {data:{session:session()}};},
    async refreshSession(){return {data:{session:session()}};},
    async signUp(){return {data:{session:null,user:{id:'new-user'}}};},
    async signInWithPassword({password}){if(password!=='correct123')return {error:{message:'Invalid login credentials'}};save(account);emit('SIGNED_IN',account);return {data:{session:account}};},
    async resetPasswordForEmail(){return {data:{}};},
    async updateUser({password}){localStorage.setItem('test-password',password);return {data:{user:account.user}};},
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
    await context.route('**/config.js', route => route.fulfill({contentType:'application/javascript',body:"globalThis.ARENA_CONFIG={SUPABASE_URL:'https://auth.example',SUPABASE_ANON_KEY:'public',API_URL:'',DEMO_MODE:false,LOCAL_MODE:false};"}));
    await context.route('https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2/+esm', route => route.fulfill({contentType:'application/javascript',body:sdk}));
    await context.route('**/js/arena.js', route => route.fulfill({contentType:'application/javascript',body:'export const WALK_SECONDS=0; export const showHome=()=>{}; export const walkToStage=async()=>{}; export const cheer=()=>{}; export const pauseRendering=()=>{}; export const startPov=()=>{}; export const walkToPodium=async()=>{};'}));
    await context.route('https://fonts.googleapis.com/**', route => route.fulfill({contentType:'text/css',body:''}));
    await context.route('**/api/me', route => route.fulfill({contentType:'application/json',body:JSON.stringify({id:'test-user',display_name:'player',prompts:[],speeches:[]})}));
    await context.route('**/api/prompts', route => route.fulfill({contentType:'application/json',body:'[]'}));

    await page.goto(base + '/progress.html');
    await page.waitForURL('**/login.html?next=progress.html');
    await page.waitForFunction(() => !document.querySelector('#auth-fields').disabled);
    await page.getByRole('button', {name:'CREATE ACCOUNT',exact:true}).click();
    await page.locator('#email').fill('player@example.com');
    await page.locator('#password').fill('correct123');
    await page.locator('#confirm-password').fill('different123');
    await page.locator('#auth-submit').click();
    await page.locator('#auth-status').filter({hasText:'Passwords do not match'}).waitFor();
    await page.locator('#confirm-password').fill('correct123');
    await page.locator('#auth-submit').click();
    await page.locator('#auth-status').filter({hasText:'Check your email'}).waitFor();
    assert.equal(await page.locator('#password').inputValue(), '');
    assert.match(page.url(), /login.html/);
    await page.screenshot({path:path.join(out,'auth-signup-desktop.png'),fullPage:true});

    await page.getByRole('button', {name:'SIGN IN',exact:true}).click();
    await page.locator('#password').fill('wrong123');
    await page.locator('#auth-submit').click();
    await page.locator('#auth-status').filter({hasText:'Invalid login credentials'}).waitFor();
    await page.locator('#forgot-password').click();
    await page.locator('#auth-submit').click();
    await page.locator('#auth-status').filter({hasText:'If an account exists'}).waitFor();
    await page.goto(base + '/login.html?mode=recovery');
    await page.locator('#auth-status').filter({hasText:'reset link is verified'}).waitFor();
    await page.locator('#password').fill('newpassword123');
    await page.locator('#confirm-password').fill('newpassword123');
    await page.locator('#auth-submit').click();
    await page.waitForURL('**/practice.html');
    assert.equal(await page.evaluate(() => localStorage.getItem('test-password')), 'newpassword123');
    await page.getByRole('button',{name:'SIGN OUT',exact:true}).click();
    await page.waitForURL('**/index.html');
    assert.equal(await page.evaluate(() => JSON.parse(localStorage.getItem('test-session'))), null);

    await page.goto(base + '/login.html?mode=recovery&expired=1');
    await page.locator('#auth-status').filter({hasText:'expired'}).waitFor();
    assert.equal(await page.locator('#password-field').isVisible(), false);
    assert.equal(await page.locator('#auth-submit').isEnabled(), true);
    await page.setViewportSize({width:390,height:844});
    await page.screenshot({path:path.join(out,'auth-reset-mobile.png'),fullPage:true});
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);

    await page.goto(base + '/login.html?next=https://evil.example');
    await page.waitForFunction(() => !document.querySelector('#auth-fields').disabled);
    await page.locator('#email').fill('player@example.com');
    await page.locator('#password').fill('correct123');
    await page.locator('#auth-submit').click();
    await page.waitForURL('**/practice.html');
    await page.goto(base + '/progress.html');
    await page.locator('#content').waitFor({state:'visible'});
    const second = await context.newPage();
    await second.goto(base + '/index.html');
    await second.getByRole('button',{name:'SIGN OUT',exact:true}).click();
    await page.waitForURL('**/login.html?next=progress.html');
    await second.close();
    await page.evaluate(() => localStorage.setItem('test-session',JSON.stringify({access_token:'guest',user:{id:'guest',is_anonymous:true}})));
    await page.goto(base + '/progress.html');
    await page.waitForURL('**/login.html?next=progress.html');
    assert.deepEqual(errors, []);
    console.log('PASS: signup confirmation, password validation, login errors, reset, recovery, logout, safe redirects, cross-tab logout, anonymous rejection, mobile layout; no page errors.');
  } finally {
    if (browser) await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(error => { console.error(error); process.exitCode=1; });
