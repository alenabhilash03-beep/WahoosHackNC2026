import test from 'node:test';
import assert from 'node:assert/strict';
import {createAccountAuth, accountSession, safeNext, loginUrl} from '../js/auth.js';

const href='https://example.com/WahoosHackNC2026/login.html';
function fixture(overrides={}) {
  const calls=[];
  const auth={
    signUp:async value=>{calls.push(['signup',value]);return {data:{session:null,user:{id:'a'}}};},
    signInWithPassword:async value=>{calls.push(['signin',value]);return {data:{session:{access_token:'a',user:{is_anonymous:false}}}};},
    resetPasswordForEmail:async (email,options)=>{calls.push(['reset',email,options]);return {data:{}};},
    updateUser:async value=>{calls.push(['update',value]);return {data:{user:{id:'a'}}};},
    signOut:async()=>{calls.push(['signout']);return {error:null};},
    ...overrides,
  };
  return {calls,service:createAccountAuth({loadClient:async()=>({auth}),href})};
}

test('signup uses the project subdirectory callback and reports email confirmation without a session',async()=>{
  const {service,calls}=fixture();
  const data=await service.signUp(' a@example.com ','password123');
  assert.equal(data.session,null);
  assert.deepEqual(calls,[['signup',{email:'a@example.com',password:'password123',options:{emailRedirectTo:href}}]]);
});
test('login errors are shown and passwords are not trimmed',async()=>{
  const {service,calls}=fixture();
  await service.signIn(' a@example.com ',' password123 ');
  assert.deepEqual(calls,[['signin',{email:'a@example.com',password:' password123 '}]]);
  const broken=fixture({signInWithPassword:async()=>({error:{message:'Invalid login credentials'}})});
  await assert.rejects(broken.service.signIn('a@example.com','wrong'),/Invalid login credentials/);
});
test('reset sends to the recovery view and update/logout propagate errors',async()=>{
  const {service,calls}=fixture();
  await service.resetPassword(' a@example.com ');
  await service.updatePassword('new password123');
  await service.signOut();
  assert.deepEqual(calls,[['reset','a@example.com',{redirectTo:href+'?mode=recovery'}],['update',{password:'new password123'}],['signout']]);
  await assert.rejects(fixture({signOut:async()=>({error:{message:'Offline'}})}).service.signOut(),/Offline/);
});
test('only account sessions are accepted',()=>{
  assert.equal(accountSession(null),null);
  assert.equal(accountSession({access_token:'a',user:{is_anonymous:true}}),null);
  const account={access_token:'a',user:{is_anonymous:false}};
  assert.equal(accountSession(account),account);
});
test('return URLs stay on known pages inside the deployed app',()=>{
  const card='card.html?id=abc';
  assert.equal(safeNext(card,href),card);
  for(const unsafe of ['https://evil.example','//evil.example','javascript:alert(1)','../other.html','/admin','login.html','%2e%2e/admin','https://example.com/other/practice.html']) {
    assert.equal(safeNext(unsafe,href),'practice.html',unsafe);
  }
  assert.equal(loginUrl('https://example.com/WahoosHackNC2026/card.html?id=abc'),href+'?next=card.html%3Fid%3Dabc');
});
