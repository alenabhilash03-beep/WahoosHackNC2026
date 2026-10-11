import test from 'node:test';
import assert from 'node:assert/strict';
import { createApiClient } from '../js/api.js';
const json = (value,status=200) => new Response(JSON.stringify(value),{status,headers:{'Content-Type':'application/json'}});
test('the client resolves a Supabase session and sends its bearer token, never an id', async () => {
  let request;
  const client=createApiClient({config:{API_URL:'https://api.example',SUPABASE_URL:'https://db.example',SUPABASE_ANON_KEY:'public'}, loadAuth:async()=>({auth:{getSession:async()=>({data:{session:{access_token:'token',user:{is_anonymous:false}}}})}}), fetchImpl:async(url,options)=>{request={url,options}; return json({id:'p'});}});
  assert.deepEqual(await client('/api/me'),{id:'p'});
  assert.equal(request.url,'https://api.example/api/me');
  assert.equal(request.options.headers.get('Authorization'),'Bearer token');
});
test('form uploads retain browser multipart boundary and errors retain API detail', async()=>{
  const form=new FormData(); form.append('side','yes');
  const client=createApiClient({config:{DEMO_MODE:true},hostname:'localhost',fetchImpl:async(url,options)=>{assert.equal(options.body,form); assert.equal(options.headers.has('Content-Type'),false); return json({detail:'Not gradable'},422);}});
  await assert.rejects(client('/api/speeches',{method:'POST',body:form}),/Not gradable/);
});
test('demo identity is refused on a public host',async()=>{
  const client=createApiClient({config:{DEMO_MODE:true},hostname:'example.com',fetchImpl:async()=>json({})});
  await assert.rejects(client('/api/me'),/local/i);
});
test('missing config and non-JSON backend fail with useful messages',async()=>{
  await assert.rejects(createApiClient({config:{}})('/api/me'),/configured/);
  const client=createApiClient({config:{DEMO_MODE:true},hostname:'127.0.0.1',fetchImpl:async()=>new Response('<html>offline</html>',{status:502})});
  await assert.rejects(client('/api/me'),/502/);
});
test('concurrent requests share the existing account session lookup',async()=>{
  let count=0;
  const client=createApiClient({config:{SUPABASE_URL:'url',SUPABASE_ANON_KEY:'key'},loadAuth:async()=>({auth:{getSession:async()=>{count++;return {data:{session:{access_token:'token',user:{is_anonymous:false}}}};}}}),fetchImpl:async()=>json({})});
  await Promise.all([client('/api/me'),client('/api/prompts')]);
  assert.equal(count,1);
});

test('signed-out and anonymous visitors cannot call the API or create guest accounts',async()=>{
  for (const session of [null,{access_token:'guest',user:{is_anonymous:true}}]) {
    let signs=0,requests=0;
    const client=createApiClient({config:{SUPABASE_URL:'url',SUPABASE_ANON_KEY:'key'},loadAuth:async()=>({auth:{getSession:async()=>({data:{session}}),signInAnonymously:async()=>{signs++;return {data:{session:{access_token:'guest'}}};}}}),fetchImpl:async()=>{requests++;return json({});}});
    await assert.rejects(client('/api/me'),error=>error.status===401 && /Sign in/.test(error.message));
    assert.equal(signs,0); assert.equal(requests,0);
  }
});
test('expired sessions refresh once; a second 401 stops without looping',async()=>{
  let refreshed=0,calls=0;
  const client=createApiClient({config:{SUPABASE_URL:'url',SUPABASE_ANON_KEY:'key'},loadAuth:async()=>({auth:{getSession:async()=>({data:{session:{access_token:'old',user:{is_anonymous:false}}}}),refreshSession:async()=>{refreshed++;return {data:{session:{access_token:'new',user:{is_anonymous:false}}}};}}}),fetchImpl:async(url,options)=>{calls++;assert.equal(options.headers.get('Authorization'),`Bearer ${calls===1?'old':'new'}`);return json({detail:'Expired'},401);}});
  await assert.rejects(client('/api/me'),/Expired/);assert.equal(refreshed,1);assert.equal(calls,2);
});
test('sign-in errors are readable and abort interrupts requests',async()=>{
  const broken=createApiClient({config:{SUPABASE_URL:'url',SUPABASE_ANON_KEY:'key'},loadAuth:async()=>({auth:{getSession:async()=>({error:{message:'Anonymous sign-ins disabled'}})}})});
  await assert.rejects(broken('/api/me'),/Anonymous sign-ins disabled/);
  const slow=createApiClient({config:{DEMO_MODE:true},hostname:'localhost',timeoutMs:5,fetchImpl:async(url,{signal})=>new Promise((resolve,reject)=>signal.addEventListener('abort',()=>reject(new Error('Aborted'))))});
  await assert.rejects(slow('/api/me'),/took too long/);
});
