// Two account sessions, real frontend and MediaRecorder, in-memory API contract.
// No hosted database, uploads or authentication providers are used.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert=require('node:assert/strict');
const http=require('node:http');
const fs=require('node:fs');
const path=require('node:path');
const root=path.resolve(__dirname,'../../web');
const out=path.resolve(__dirname,'../../work/browser-qa');
const report=JSON.parse(fs.readFileSync(path.resolve(__dirname,'../../softskills-grader/sample_report.json'),'utf8'));
const sdk=`export const createClient=()=>{const session={access_token:localStorage.getItem('account'),user:{id:localStorage.getItem('account'),is_anonymous:false}};return {auth:{getSession:async()=>({data:{session}}),refreshSession:async()=>({data:{session}}),onAuthStateChange:()=>({data:{subscription:{unsubscribe(){}}}})}};};`;
const counts=()=>({love:0,like:0,fire:0,amazing:0});
let round={id:'r1',starts_at:new Date(Date.now()-60000).toISOString(),ends_at:new Date(Date.now()+3600000).toISOString(),prompt:{id:'m1',text:'Should schools require uniforms?'}};
let entry=null, moderation='pending', enabled=true, failReaction=false, failBoard=false, holdBoard=null, heldResolve=null;
let votes={}, lastUpload, audioRequests=0, boardRequests=0, extraEntry=false, reverseOrder=false, audioExpiresAt=null;
function result(user){const breakdown=counts();for(const value of Object.values(votes))breakdown[value]++;return {counts:breakdown,total_reactions:Object.keys(votes).length,my_reaction:votes[user]??null};}
function row(user){return {id:'e1',speech_id:'s1',rank:1,display_name:'Ada',side:'yes',overall:95.7,letter:'A',is_owner:user==='speaker',...result(user)};}
function board(user){
  const entries=entry&&!entry.withdrawn_at&&moderation==='approved'?[row(user)]:[];
  if(entries.length&&extraEntry){entries.push({...row(user),id:'e2',speech_id:'s2',display_name:'Grace'});if(reverseOrder)entries.reverse();entries.forEach((e,i)=>e.rank=i+1);}
  return {round,entries,has_more:false};
}
function card(){return {id:'s1',status:'done',motion_id:'m1',side:'yes',prompt:round?.prompt,report,audio_url:null,community_round_id:'r1',audio_expires_at:audioExpiresAt,moderation_status:moderation,fixes:[]};}
(async()=>{
  fs.mkdirSync(out,{recursive:true});
  const server=http.createServer((req,res)=>{
    const file=path.resolve(root,'.'+new URL(req.url,'http://localhost').pathname);
    if(!file.startsWith(root+path.sep)){res.writeHead(404).end();return;}
    try{res.setHeader('Content-Type',({'.js':'application/javascript','.css':'text/css','.html':'text/html','.wav':'audio/wav'})[path.extname(file)]||'application/octet-stream');res.end(fs.readFileSync(file));}catch{res.writeHead(404).end();}
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const base=`http://127.0.0.1:${server.address().port}`;
  let browser;const errors=[];
  try{
    browser=await chromium.launch({headless:true,...(process.env.BROWSER_EXECUTABLE?{executablePath:process.env.BROWSER_EXECUTABLE}:{}),...(process.env.BROWSER_CHANNEL?{channel:process.env.BROWSER_CHANNEL}:{}),args:['--use-fake-device-for-media-stream','--use-fake-ui-for-media-stream']});
    async function session(user){
      const context=await browser.newContext({viewport:{width:1280,height:1000},permissions:['microphone']});
      await context.addInitScript(value=>localStorage.setItem('account',value),user);
      await context.route('**/config.js',r=>r.fulfill({contentType:'application/javascript',body:`globalThis.ARENA_CONFIG={SUPABASE_URL:'https://auth.example',SUPABASE_ANON_KEY:'public',API_URL:'',COMMUNITY_ENABLED:${enabled}};`}));
      await context.route('https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2/+esm',r=>r.fulfill({contentType:'application/javascript',body:sdk}));
      await context.route('**/js/arena.js',r=>r.fulfill({contentType:'application/javascript',body:'export const cheer=()=>{};export const pauseRendering=()=>{};export const startPov=()=>{};export const walkToPodium=async()=>{};'}));
      await context.route('**/api/**',async r=>{
        const url=new URL(r.request().url()),name=url.pathname,body=r.request().postDataJSON?.bind(r.request());
        const json=(data,status=200)=>r.fulfill({status,contentType:'application/json',body:JSON.stringify(data)});
        assert.equal(r.request().headers().authorization,`Bearer ${user}`);
        if(name==='/api/leaderboard')return json({boards:{beginner:{rows:[],you:null},intermediate:{rows:[],you:null},expert:{rows:[],you:null}},display_name:'Ada'});
        if(name==='/api/prompts')return json([{id:'m1',text:'Should schools require uniforms?',featured:true}]);
        if(name==='/api/community/round')return json({round,entry:user==='speaker'?entry:null,display_name:'Ada'});
        if(name==='/api/community/leaderboard'){
          boardRequests++;
          if(failBoard)return json({detail:'Connection unavailable'},503);
          const snapshot=board(user);
          assert.ok(['grade','favorites'].includes(url.searchParams.get('sort')));assert.equal(url.searchParams.get('round_id'),round.id);
          if(holdBoard){heldResolve();await holdBoard;}
          return json(snapshot);
        }
        if(name==='/api/community/entries'){
          assert.deepEqual(body(),{speech_id:'s1'});entry={id:'e1',speech_id:'s1',withdrawn_at:null};return json({entry},201);
        }
        if(name==='/api/community/entries/e1/visibility'){
          entry.withdrawn_at=body().shared?null:new Date().toISOString();return json({entry});
        }
        if(name==='/api/community/speeches/s1/reaction'){
          if(user==='speaker')return json({detail:'Cannot react to your own speech'},403);
          if(failReaction)return json({detail:'Network interrupted'},503);
          const value=body().reaction;assert.ok(['love','like','fire','amazing',null].includes(value));
          if(value===null)delete votes[user];else votes[user]=value;
          return json(result(user));
        }
        if(name==='/api/community/speeches/s1/audio'){
          audioRequests++;return json({audio_url:`${base}/test-audio.wav?signature=${audioRequests}`});
        }
        if(name==='/api/speeches/s1/coaching')return json({lines:{}});
        if(name==='/api/speeches/s1')return json({...card(),audio_url:`${base}/test-audio.wav`});
        if(name==='/api/speeches'){
          lastUpload=r.request().postDataBuffer().toString();assert.match(lastUpload,/name="audio"/);return json({speech_id:'s1'},202);
        }
        return json({detail:'Unexpected route: '+name},404);
      });
      await context.route('**/test-audio.wav*',r=>{
        // 60 seconds of silence, long enough to observe uninterrupted playback.
        const bytes=Buffer.alloc(44+8000*2*60);bytes.write('RIFF');bytes.writeUInt32LE(bytes.length-8,4);bytes.write('WAVEfmt ',8);bytes.writeUInt32LE(16,16);bytes.writeUInt16LE(1,20);bytes.writeUInt16LE(1,22);bytes.writeUInt32LE(8000,24);bytes.writeUInt32LE(16000,28);bytes.writeUInt16LE(2,32);bytes.writeUInt16LE(16,34);bytes.write('data',36);bytes.writeUInt32LE(bytes.length-44,40);
        return r.fulfill({contentType:'audio/wav',body:bytes});
      });
      const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));return {context,page};
    }
    const speaker=await session('speaker'),listener=await session('listener');const a=speaker.page,b=listener.page;
    await a.goto(`${base}/card.html?id=s1`);await a.getByRole('button',{name:'Upload to the community board',exact:true}).click();
    await a.locator('#share-status').filter({hasText:'Submitted for review'}).waitFor();assert.ok(entry);assert.equal(moderation,'pending');
    await b.goto(`${base}/leaderboard.html`);await b.locator('#community-status').filter({hasText:'No shared speeches'}).waitFor();
    moderation='approved';await a.locator('#share-retry').click();
    await a.locator('#share-status').filter({hasText:'Shared with the community'}).waitFor();
    await b.locator('#community-retry').click();await b.getByRole('button',{name:'Fire: 0 reactions',exact:true}).waitFor();assert.equal(audioRequests,0);
    await b.getByRole('button',{name:'Fire: 0 reactions',exact:true}).click();await b.getByRole('button',{name:'Fire: 1 reactions',exact:true}).waitFor();
    await b.getByRole('button',{name:'Love: 0 reactions',exact:true}).click();await b.getByRole('button',{name:'Love: 1 reactions',exact:true}).waitFor();assert.equal(Object.keys(votes).length,1);
    await b.getByRole('button',{name:'Love: 1 reactions',exact:true}).click();await b.getByRole('button',{name:'Love: 0 reactions',exact:true}).waitFor();assert.equal(Object.keys(votes).length,0);
    await b.locator('#community-favorites').click();await b.getByRole('button',{name:'Fire: 0 reactions',exact:true}).waitFor();
    await b.getByRole('button',{name:'Fire: 0 reactions',exact:true}).click();await b.getByRole('button',{name:'Fire: 1 reactions',exact:true}).waitFor();
    await b.reload();await b.getByRole('button',{name:'Fire: 1 reactions',exact:true}).waitFor();assert.equal(await b.getByRole('button',{name:'Fire: 1 reactions',exact:true}).getAttribute('aria-pressed'),'true');
    failReaction=true;await b.getByRole('button',{name:'Like: 0 reactions',exact:true}).click();await b.locator('.community-entry [role=status]').filter({hasText:'Showing your previous reaction'}).waitFor();assert.equal(await b.getByRole('button',{name:'Fire: 1 reactions',exact:true}).getAttribute('aria-pressed'),'true');failReaction=false;
    // Hold a poll snapshot across a vote. The old count cannot undo the vote.
    let release;const held=new Promise(resolve=>heldResolve=resolve);holdBoard=new Promise(resolve=>release=resolve);
    await b.locator('#community-retry').click();await held;
    await b.getByRole('button',{name:'Amazing: 0 reactions',exact:true}).click();await b.getByRole('button',{name:'Amazing: 1 reactions',exact:true}).waitFor();
    holdBoard=null;release();await b.waitForTimeout(100);assert.equal(await b.getByRole('button',{name:'Amazing: 1 reactions',exact:true}).getAttribute('aria-pressed'),'true');
    await b.getByRole('button',{name:'Load recording',exact:true}).click();await b.locator('.community-entry audio').waitFor({state:'visible'});assert.equal(audioRequests,1);
    await b.locator('.community-entry audio').evaluate(async audio=>{window.playingAudio=audio;await audio.play();});
    await b.waitForTimeout(200);await b.locator('#community-retry').click();await b.waitForTimeout(200);
    assert.equal(await b.evaluate(()=>window.playingAudio===document.querySelector('.community-entry audio')&&!window.playingAudio.paused),true);assert.equal(audioRequests,1);
    extraEntry=true;reverseOrder=true;await b.locator('#community-retry').click();await b.locator('[data-speech-id=s1] h3').filter({hasText:'#2'}).waitFor();
    reverseOrder=false;await b.locator('#community-retry').click();await b.locator('[data-speech-id=s1] h3').filter({hasText:'#1'}).waitFor();
    assert.equal(await b.evaluate(()=>window.playingAudio===document.querySelector('[data-speech-id=s1] audio')&&!window.playingAudio.paused&&window.playingAudio.currentTime>0),true);
    extraEntry=false;await b.locator('#community-retry').click();await b.waitForFunction(()=>document.querySelectorAll('.community-entry').length===1);
    await b.locator('.community-entry audio').evaluate(audio=>audio.dispatchEvent(new Event('error')));await b.locator('.community-entry [role=status]').filter({hasText:'expired'}).waitFor();
    await b.getByRole('button',{name:'Refresh recording link',exact:true}).click();await b.locator('.community-entry [role=status]').filter({hasText:'Recording ready'}).waitFor();assert.equal(audioRequests,2);
    await a.goto(`${base}/leaderboard.html`);await a.locator('.reactions button').first().waitFor();assert.equal(await a.locator('.reactions button').first().isDisabled(),true);
    // Keyboard tab navigation, mobile layout, literal display strings.
    await b.locator('#community-grade').focus();await b.keyboard.press('ArrowRight');assert.equal(await b.locator('#community-favorites').getAttribute('aria-selected'),'true');
    await b.getByRole('button',{name:'Amazing: 1 reactions',exact:true}).waitFor();
    await b.screenshot({path:path.join(out,'community-desktop.png'),fullPage:true});
    await b.setViewportSize({width:390,height:844});assert.equal(await b.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    await b.screenshot({path:path.join(out,'community-mobile.png'),fullPage:true});
    // Poll scheduling only while visible (clock avoids a 30 second wall wait).
    await b.clock.install();
    await b.evaluate(()=>{window.testHidden=true;Object.defineProperty(document,'hidden',{configurable:true,get:()=>window.testHidden});document.dispatchEvent(new Event('visibilitychange'));});
    const requests=boardRequests;await b.clock.fastForward(31000);assert.equal(boardRequests,requests);
    await b.evaluate(()=>{window.testHidden=false;document.dispatchEvent(new Event('visibilitychange'));});await b.waitForTimeout(100);assert.ok(boardRequests>requests);
    await b.clock.resume();
    failBoard=true;await b.locator('#community-retry').click();await b.locator('#community-error').filter({hasText:'Connection unavailable'}).waitFor();failBoard=false;
    await a.goto(`${base}/card.html?id=s1`);await a.getByRole('button',{name:'Remove from the community board',exact:true}).click();await a.getByRole('button',{name:'Upload again',exact:true}).waitFor();
    await b.locator('#community-retry').click();await b.locator('#community-status').filter({hasText:'No shared speeches'}).waitFor();
    await a.getByRole('button',{name:'Upload again',exact:true}).click();await a.getByRole('button',{name:'Remove from the community board',exact:true}).waitFor();
    moderation='rejected';await a.locator('#share-retry').click();await a.locator('#share-status').filter({hasText:'Review rejected'}).waitFor();moderation='approved';
    // Community recording tags the upload; ordinary retry never adds a round ID.
    await a.goto(`${base}/practice.html?mode=community`);await a.locator('#community-retention').filter({hasText:'grade stays saved'}).waitFor();await a.locator('[data-side=yes]').click();
    await a.locator('#speaking').waitFor({state:'visible',timeout:15000});await a.waitForTimeout(300);await a.locator('#finish').click();await a.locator('.stats-frame').waitFor();assert.match(lastUpload,/name="community_round_id"\r\n\r\nr1/);
    await a.goto(`${base}/practice.html?motion_id=m1&side=yes`);await a.locator('#speaking').waitFor({state:'visible',timeout:15000});await a.waitForTimeout(300);await a.locator('#finish').click();await a.locator('.stats-frame').waitFor();assert.doesNotMatch(lastUpload,/community_round_id/);
    audioExpiresAt=new Date(Date.now()+1500).toISOString();await a.goto(`${base}/card.html?id=s1`);await a.locator('#audio-status').filter({hasText:'recording has expired'}).waitFor();assert.equal(await a.locator('#audio').isVisible(),false);assert.equal(await a.locator('#score').innerText(),'95.7');audioExpiresAt=null;
    // Round rotation removes old playback/selection even when the prompt repeats.
    await b.locator('#community-retry').click();await b.locator('.community-entry').waitFor();
    await b.getByRole('button',{name:'Load recording',exact:true}).click();await b.locator('.community-entry audio').evaluate(audio=>window.oldAudio=audio);
    entry=null;votes={};round={...round,id:'r2'};
    await b.locator('#community-retry').click();await b.locator('#community-status').filter({hasText:'No shared speeches'}).waitFor();assert.equal(await b.evaluate(()=>window.oldAudio.paused&&!window.oldAudio.hasAttribute('src')),true);
    round={...round,ends_at:new Date(Date.now()-1).toISOString()};await b.locator('#community-retry').click();await b.locator('#community-status').filter({hasText:'has closed'}).waitFor();
    round=null;await b.locator('#community-retry').click();await b.locator('#community-status').filter({hasText:'No current community round'}).waitFor();
    // Feature gate: no new API calls from the regular pages when disabled.
    enabled=false;const before=boardRequests;await b.reload();assert.equal(await b.locator('#community').isVisible(),false);assert.equal(boardRequests,before);
    await a.goto(`${base}/practice.html?mode=community`);await a.locator('#status').filter({hasText:'not available yet'}).waitFor();assert.equal(await a.locator('#round').isVisible(),false);
    assert.deepEqual(errors,[]);
    console.log('Community browser checks passed: two sessions, review, share/withdraw, recording tags, reaction change/removal/failure, stale poll, reload, playback preservation/expiry, keyboard, mobile, visibility, rotation and disabled feature.');
  }finally{if(browser)await browser.close();await new Promise(resolve=>server.close(resolve));}
})().catch(error=>{console.error(error);process.exitCode=1;});
