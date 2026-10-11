import test from 'node:test';
import assert from 'node:assert/strict';
import {createAccountAuth, accountSession, safeNext, loginUrl} from '../js/auth.js';
import * as authModule from '../js/auth.js';

const href='https://example.com/WahoosHackNC2026/login.html';
function fixture(overrides={}) {
  const calls=[];
  const auth={
    signOut:async value=>{calls.push(['signout',value]);return {error:null};},
    signInWithOAuth:async value=>{calls.push(['oauth',value]);return {data:{url:'https://provider.example'}};},
    ...overrides,
  };
  return {calls,service:createAccountAuth({loadClient:async()=>({auth}),href})};
}

test('logout clears only the local session and propagates failures',async()=>{
  const {service,calls}=fixture();
  await service.signOut();
  assert.deepEqual(calls,[['signout',{scope:'local'}]]);
  await assert.rejects(fixture({signOut:async()=>({error:{message:'Offline'}})}).service.signOut(),/Offline/);
});
test('only account sessions are accepted',()=>{
  assert.equal(accountSession(null),null);
  assert.equal(accountSession({access_token:'a',user:{is_anonymous:true}}),null);
  const account={access_token:'a',user:{is_anonymous:false}};
  assert.equal(accountSession(account),account);
});
test('Google and GitHub use an exact callback with no untrusted return query',async()=>{
  const {service,calls}=fixture();
  for(const provider of ['google','github']) await service.signInWithProvider(provider);
  assert.deepEqual(calls,['google','github'].map(provider=>['oauth',{provider,options:{redirectTo:href}}]));
  await assert.rejects(async()=>service.signInWithProvider('evil'),/Unsupported sign-in provider/);
  await assert.rejects(fixture({signInWithOAuth:async()=>({error:{message:'Provider unavailable'}})}).service.signInWithProvider('google'),/Provider unavailable/);
});
test('provider discovery uses public settings and only enables supported providers',async()=>{
  const config={SUPABASE_URL:'https://project.supabase.co',SUPABASE_ANON_KEY:'publishable'};
  const requests=[];
  const fetchImpl=async(url,options)=>{
    requests.push([String(url),options]);
    return {ok:true,json:async()=>({external:{google:true,github:false,apple:true}})};
  };
  assert.deepEqual(await authModule.getLoginProviders({config,fetchImpl}),['google']);
  assert.deepEqual(requests,[['https://project.supabase.co/auth/v1/settings',{headers:{apikey:'publishable'},credentials:'omit'}]]);
  await assert.rejects(authModule.getLoginProviders({config,fetchImpl:async()=>({ok:false})}),/Could not load sign-in options/);
  await assert.rejects(authModule.getLoginProviders({config:{},fetchImpl}),/Sign-in is not configured/);
  assert.deepEqual(await authModule.getLoginProviders({config,fetchImpl:async()=>({ok:true,json:async()=>({})})}),[]);
});
test('return URLs stay on known pages inside the deployed app',()=>{
  const card='card.html?id=abc';
  assert.equal(safeNext(card,href),card);
  assert.equal(safeNext('leaderboard.html',href),'leaderboard.html');
  for(const unsafe of ['https://evil.example','//evil.example','javascript:alert(1)','../other.html','/admin','login.html','%2e%2e/admin','https://example.com/other/practice.html']) {
    assert.equal(safeNext(unsafe,href),'practice.html',unsafe);
  }
  assert.equal(loginUrl('https://example.com/WahoosHackNC2026/card.html?id=abc'),href+'?next=card.html%3Fid%3Dabc');
});
