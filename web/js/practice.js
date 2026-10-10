import {api} from './api.js';
import {createRecording} from './recorder.js';
const $=id=>document.getElementById(id);
let ready=false,busy=false,recorded,recordedName='recording.webm',demo=false;
function controls(state=recorder.state) {
  const locked=busy || state!=='idle';
  $('prompt').disabled=!ready || locked;$('sides').disabled=locked;
  $('start').disabled=!ready || demo || locked;$('file').disabled=!ready || demo || locked;
  $('send').disabled=!ready || demo || locked || !recorded;
  $('stop').hidden=state==='idle' || state==='stopping';
}
const recorder=createRecording({
  onUpdate({state,seconds,error}) {
    $('timer').textContent=state==='idle'?'60':state==='acquiring'?'…':String(seconds);
    if(error)$('recording-status').textContent=error;
    else if(state==='acquiring')$('recording-status').textContent='Waiting for microphone permission…';
    else if(state==='countdown')$('recording-status').textContent='Microphone ready. Get ready…';
    else if(state==='recording')$('recording-status').textContent='Recording. Make your case.';
    controls(state);
  },
  onCaptured(blob) { recorded=blob;recordedName=blob.type.includes('mp4')?'recording.mp4':blob.type.includes('ogg')?'recording.ogg':'recording.webm';send(); },
});
async function load() {
  ready=false;$('reload').hidden=true;$('status').textContent='Loading prompts…';controls();
  try {
    const prompts=await api('/api/prompts');demo=Boolean(globalThis.ARENA_CONFIG?.DEMO_MODE);
    $('prompt').replaceChildren();for(const p of prompts)$('prompt').add(new Option(p.text,p.id));
    const params=new URLSearchParams(location.search);
    if(prompts.some(p=>p.id===params.get('motion_id')))$('prompt').value=params.get('motion_id');
    const side=params.get('side');if(['yes','no'].includes(side))document.querySelector(`input[value="${side}"]`).checked=true;
    ready=prompts.length>0;$('status').textContent=ready?(demo?'LOCAL PREVIEW · Connect live services to grade recordings.':'Private practice. Your recording and report are visible only to you.'):'No prompts available. Seed prompts on the server, then retry.';
    $('preview').hidden=!demo;$('reload').hidden=ready;controls();
  }catch(error){$('status').textContent=error.message;$('reload').hidden=false;controls();}
}
async function send() {
  if(!recorded || busy)return;
  if(recorded.size>15*1024*1024){$('recording-status').textContent='The recording exceeds 15 MB. Choose a smaller file.';return;}
  busy=true;controls();$('recording-status').textContent='Uploading your speech…';
  try {
    const form=new FormData();form.append('motion_id',$('prompt').value);form.append('side',document.querySelector('input[name=side]:checked').value);form.append('audio',recorded,recordedName);
    const result=await api('/api/speeches',{method:'POST',body:form});
    if(!result.speech_id)throw new Error('The server returned no speech id. Please retry.');
    location.href=`card.html?id=${encodeURIComponent(result.speech_id)}`;
  }catch(error){$('recording-status').textContent=`${error.message} Your recording is kept here; retry Upload + Grade.`;}
  finally{busy=false;controls();}
}
$('start').addEventListener('click',()=>{recorded=null;$('file').value='';recorder.start();});
$('stop').addEventListener('click',()=>{const cancel=recorder.state!=='recording';recorder.stop();if(cancel)$('recording-status').textContent='Recording cancelled. Ready when you are.';});
$('file').addEventListener('change',()=>{recorded=$('file').files[0] ?? null;recordedName=recorded?.name ?? 'recording.wav';$('recording-status').textContent=recorded?'File ready. Upload to grade.':'Ready when you are.';controls();});
$('send').addEventListener('click',send);$('reload').addEventListener('click',load);
window.addEventListener('pagehide',()=>recorder.destroy());
load();
