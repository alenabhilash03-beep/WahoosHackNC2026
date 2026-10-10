import test from 'node:test';
import assert from 'node:assert/strict';
import { createRecording } from '../js/recorder.js';
function setup(options={}) {
  let clock=0, tick, stops=0, output, updates=[];
  const stream={getTracks:()=>[{stop(){stops++;}}]};
  class Recorder {
    constructor(){this.state='inactive';this.mimeType='audio/webm';}
    start(){this.state='recording';}
    stop(){this.state='inactive';this.ondataavailable?.({data:new Blob(['speech'])});this.onstop?.();}
  }
  const controller=createRecording({getUserMedia:async()=>stream,Recorder,now:()=>clock,setIntervalImpl:f=>(tick=f,1),clearIntervalImpl:()=>{tick=null;},countdownMs:10000,durationMs:60000,onUpdate:u=>updates.push(u),onCaptured:b=>{output=b;},...options});
  return {controller,advance(t){clock=t;tick?.();},get stops(){return stops;},get output(){return output;},get updates(){return updates;}};
}
test('countdown precedes recording, automatic stop produces audio and releases the mic',async()=>{
  const x=setup(); await x.controller.start();
  assert.equal(x.controller.state,'countdown');x.advance(10000);assert.equal(x.controller.state,'recording');
  x.advance(70000);assert.equal(x.controller.state,'idle');assert.equal(x.stops,1);assert.equal(x.output.type,'audio/webm');assert.equal(x.output.size,6);
});
test('stop during countdown cancels without uploading and stop early captures audio',async()=>{
  const x=setup();await x.controller.start();x.controller.stop();assert.equal(x.output,undefined);assert.equal(x.stops,1);
  const y=setup();await y.controller.start();y.advance(10000);y.controller.stop();assert.ok(y.output);
});
test('cancel during microphone permission request releases the eventual stream',async()=>{
  let resolve;let stops=0;
  const x=setup({getUserMedia:()=>new Promise(r=>resolve=r)});
  const started=x.controller.start();x.controller.stop();resolve({getTracks:()=>[{stop(){stops++;}}]});await started;
  assert.equal(stops,1);assert.equal(x.controller.state,'idle');assert.equal(x.output,undefined);
});
test('permission denied becomes a useful idle error and allows retry',async()=>{
  const x=setup({getUserMedia:async()=>{throw Object.assign(new Error('denied'),{name:'NotAllowedError'});}});
  await x.controller.start();assert.equal(x.controller.state,'idle');assert.match(x.updates.at(-1).error,/permission/i);
});
