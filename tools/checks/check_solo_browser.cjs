const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const base=process.argv[2] || 'http://127.0.0.1:8000';
assert.ok(['localhost','127.0.0.1','[::1]'].includes(new URL(base).hostname),'Run against the local DEMO_MODE server only.');
const out=require('node:path').resolve(__dirname,'../../work/browser-qa');
fs.mkdirSync(out,{recursive:true});
(async()=>{
  const browser=await chromium.launch({...(process.env.BROWSER_CHANNEL ? {channel:process.env.BROWSER_CHANNEL} : {}),headless:true,args:['--use-fake-device-for-media-stream','--use-fake-ui-for-media-stream']});
  const errors=[];
  try {
    const context=await browser.newContext({viewport:{width:1440,height:1000},permissions:['microphone']});
    const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
    await page.goto(`${base}/card.html?id=demo-best`);await page.locator('#card').waitFor({state:'visible'});
    const metrics=await page.locator('.metric').count();assert.equal(metrics,24);
    assert.equal(await page.locator('#score').innerText(),'95.7');
    assert.match(await page.locator('#comparison').innerText(),/\+15/);
    assert.equal(await page.locator('#fixes li').count(),3);
    assert.equal(await page.locator('#transcript .filler').count(),4);
    await page.locator('#transcript button.filler').first().click();
    await page.waitForFunction(()=>document.querySelector('#audio').currentTime>5);
    await page.locator('#audio-status').filter({hasText:'Playing from'}).waitFor();
    await page.locator('#audio').evaluate(a=>a.pause());
    await page.screenshot({path:`${out}/phase-2-card-desktop.png`,fullPage:true});
    await page.locator('.radar-toggle').click();assert.equal(await page.locator('.radar-table:visible tbody tr').count(),5);
    await page.goto(`${base}/progress.html`);await page.locator('#content').waitFor({state:'visible'});
    assert.match(await page.locator('#meta').innerText(),/2 graded attempts/);
    assert.equal(await page.locator('#gains li').count(),5);assert.equal(await page.locator('#history li').count(),2);
    await page.selectOption('#prompt','demo-ai-pacing');assert.match(await page.locator('#best').innerText(),/95.7/);
    await page.screenshot({path:`${out}/phase-2-progress-desktop.png`,fullPage:true});
    await page.goto(`${base}/card.html?id=demo-ungradable`);await page.locator('#status-heading').filter({hasText:'NOT GRADABLE'}).waitFor();
    assert.match(await page.locator('#status').innerText(),/40 words/);
    await page.goto(`${base}/card.html?id=missing`);await page.locator('#status-heading').filter({hasText:'CARD UNAVAILABLE'}).waitFor();assert.match(await page.locator('#status').innerText(),/not found/i);
    await page.goto(`${base}/practice.html`);await page.locator('#preview').waitFor({state:'visible'});assert.equal(await page.locator('#start').isDisabled(),true);
    for(const path of ['card.html?id=demo-best','progress.html','practice.html']){
      await page.setViewportSize({width:390,height:844});await page.goto(`${base}/${path}`);
      await page.locator(path.startsWith('card')?'#card':path.startsWith('progress')?'#content':'#preview').waitFor({state:'visible'});
      const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>window.innerWidth);
      assert.equal(overflow,false,`mobile overflow on ${path}`);
      if(path.startsWith('card'))await page.screenshot({path:`${out}/phase-2-card-mobile.png`,fullPage:true});
    }
    // Exercise actual MediaRecorder with a browser-provided synthetic mic, while
    // replacing only authentication and upload (no provider credentials required).
    await context.route('**/config.js',r=>r.fulfill({contentType:'application/javascript',body:"globalThis.ARENA_CONFIG={API_URL:'',SUPABASE_URL:'https://test.example',SUPABASE_ANON_KEY:'public-test',DEMO_MODE:false};"}));
    await context.route('https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2/+esm',r=>r.fulfill({contentType:'application/javascript',body:"export const createClient=()=>({auth:{getSession:async()=>({data:{session:{access_token:'local-demo'}}})}});"}));
    let uploaded=false;
    await context.route('**/api/speeches',async r=>{
      if(r.request().method()!=='POST')return r.continue();
      const body=r.request().postDataBuffer();assert.ok(body?.length>100);assert.match(body.toString(),/name="motion_id"/);uploaded=true;
      await r.fulfill({status:202,contentType:'application/json',body:'{"speech_id":"demo-best"}'});
    });
    await page.goto(`${base}/practice.html?motion_id=demo-ai-pacing&side=no`);await page.locator('#start').waitFor({state:'visible'});await page.waitForFunction(()=>!document.querySelector('#start').disabled);
    assert.equal(await page.locator('input[value=no]').isChecked(),true);
    await page.locator('#start').click();await page.locator('#recording-status').filter({hasText:'Microphone ready'}).waitFor();await page.locator('#stop').click();assert.equal(uploaded,false);
    await page.locator('#start').click();await page.locator('#recording-status').filter({hasText:'Recording. Make your case.'}).waitFor({timeout:15000});
    await page.waitForTimeout(800);await page.locator('#stop').click();await page.waitForURL('**/card.html?id=demo-best');assert.equal(uploaded,true);
    await page.locator('#card').waitFor({state:'visible'});
    assert.deepEqual(errors,[]);
    console.log(JSON.stringify({checks:'card,24 rubric rows,fixes,deltas,radar,seek,progress,prompt filter,ungradable,missing,mobile overflow,mic cancel,countdown,recording,upload',pageErrors:errors,screenshots:3},null,2));
  }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
