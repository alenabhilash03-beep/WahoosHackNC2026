// Clock-based countdown and recording, with cancellation safe during permission prompts.
export function createRecording({getUserMedia=()=>navigator.mediaDevices.getUserMedia({audio:true}),
  Recorder=globalThis.MediaRecorder,now=()=>performance.now(),setIntervalImpl=setInterval,
  clearIntervalImpl=clearInterval,countdownMs=10000,durationMs=60000,
  onUpdate=()=>{},onCaptured=()=>{}}={}) {
  let state='idle',stream,recorder,interval,deadline,generation=0,chunks=[];
  const update=(error)=>onUpdate({state,seconds:deadline?Math.max(0,Math.ceil((deadline-now())/1000)):0,error});
  function release() { if(interval)clearIntervalImpl(interval);interval=null;stream?.getTracks().forEach(t=>t.stop());stream=null;deadline=null; }
  function cancel() {
    generation++;
    if(recorder?.state==='recording') { recorder.onstop=null;recorder.stop(); }
    release();state='idle';update();
  }
  function stop() {
    if (state==='recording') { state='stopping';update();recorder.stop(); }
    else cancel();
  }
  function tick() {
    if (now()>=deadline) {
      if(state==='countdown') {
        try { recorder.start();state='recording';deadline=now()+durationMs; }
        catch(error) { release();state='idle';update(error.message);return; }
      } else if(state==='recording') { stop();return; }
    }
    update();
  }
  return {
    get state(){return state;},
    async start() {
      if(state!=='idle')return;
      const run=++generation;state='acquiring';update();
      try {
        if(!Recorder)throw new Error('This browser cannot record audio. Upload an audio file instead.');
        const acquired=await getUserMedia();
        if(run!==generation) { acquired.getTracks().forEach(t=>t.stop());return; }
        stream=acquired;recorder=new Recorder(stream);chunks=[];
        recorder.ondataavailable=event=>{if(event.data.size)chunks.push(event.data);};
        recorder.onstop=()=>{
          const blob=new Blob(chunks,{type:recorder.mimeType || chunks[0]?.type || 'audio/webm'});
          release();state='idle';update();
          if(blob.size)onCaptured(blob);else update('The recording was empty. Try again.');
        };
        recorder.onerror=()=>{cancel();update('The microphone recording failed. Please retry.');};
        state='countdown';deadline=now()+countdownMs;update();interval=setIntervalImpl(tick,100);
      } catch(error) {
        if(run!==generation)return;
        release();state='idle';update(error.name==='NotAllowedError'?'Microphone permission was denied. Allow access or upload an audio file.':error.message);
      }
    },stop,destroy:cancel,
  };
}
