import {communityEnabled, shareState, RETENTION_NOTICE} from './community-model.js';

export function mountCommunityShare({api, root=document.getElementById('community-share')} = {}) {
  let card, data, pending=false, generation=0, alive=true;
  if(!root || !communityEnabled()) return {update:()=>{}};
  const $=id=>root.querySelector(`#${id}`);
  $('share-retention').textContent=RETENTION_NOTICE;
  function draw() {
    const state=shareState(card,data);
    $('share-retention').hidden=!card.community_round_id;
    $('share-status').textContent=state.message;
    $('share-name').hidden=!state.needsName;
    $('share-action').hidden=!state.action;
    $('share-action').textContent=state.label ?? '';
    $('share-action').disabled=pending;
    $('share-retry').disabled=pending;
  }
  async function refresh() {
    const stamp=++generation; data=null; root.hidden=false; $('share-action').hidden=true;
    $('share-status').textContent='Checking sharing eligibility…';
    try {
      const [result,latest]=await Promise.all([api('/api/community/round'),api(`/api/speeches/${encodeURIComponent(card.id)}`)]);
      if(alive && stamp===generation){data=result;card=latest;draw();$('share-error').textContent='';}
    }
    catch(error){if(alive && stamp===generation){$('share-error').textContent=`${error.message} Retry to check sharing.`;$('share-status').textContent='Sharing unavailable.';}}
  }
  $('share-action').addEventListener('click',async()=>{
    if(pending || !data || !card) return;
    const state=shareState(card,data);if(!state.action)return;
    pending=true;draw();$('share-error').textContent='';
    try {
      if(state.action==='share') await api('/api/community/entries',{method:'POST',body:{speech_id:card.id}});
      else await api(`/api/community/entries/${encodeURIComponent(data.entry.id)}/visibility`,{method:'POST',body:{shared:state.action==='reshare'}});
      await refresh();
    } catch(error) {if(alive)$('share-error').textContent=`${error.message} Try again. Sharing status will be checked before the next request.`;await refreshAfterFailure();}
    finally {pending=false;if(alive){$('share-retry').disabled=false;if(data)draw();}}
  });
  async function refreshAfterFailure() {
    // A response can be lost after the server commits. Reconcile without hiding
    // the error or assuming a failed request means visibility did not change.
    try {const result=await api('/api/community/round');if(alive)data=result;}catch{data=null;$('share-action').hidden=true;}
  }
  $('share-retry').addEventListener('click',()=>{if(!pending)refresh();});
  const clock=setInterval(()=>{if(alive && data && card && !document.hidden)draw();},1000);
  window.addEventListener('pagehide',()=>{alive=false;++generation;clearInterval(clock);});
  return {update(value){card=value;$('share-retention').hidden=!value.community_round_id;if(alive)refresh();}};
}
