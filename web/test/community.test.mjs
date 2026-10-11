import test from 'node:test';
import assert from 'node:assert/strict';
import {communityEnabled, roundState, closingLine, nextReaction, shareState, createBoardState} from '../js/community-model.js';

const now=Date.now();
const round={id:'round',starts_at:new Date(now-60000).toISOString(),ends_at:new Date(now+600000).toISOString()};
const card={id:'speech',status:'done',community_round_id:'round',audio_url:'https://test/audio',report:{gradable:true},moderation_status:'pending'};
const data={round,entry:null,display_name:'Ada'};
test('community needs an explicit feature flag and a real account mode',()=>{
  assert.equal(communityEnabled({}),false);
  assert.equal(communityEnabled({COMMUNITY_ENABLED:'true'}),false);
  assert.equal(communityEnabled({COMMUNITY_ENABLED:true}),true);
  for(const mode of ['LOCAL_MODE','DEMO_MODE'])assert.equal(communityEnabled({COMMUNITY_ENABLED:true,[mode]:true}),false);
});
test('round boundaries, malformed metadata and no round fail closed',()=>{
  assert.equal(roundState(round,now),'active');
  assert.equal(roundState(round,Date.parse(round.ends_at)),'closed');
  assert.equal(roundState(round,Date.parse(round.starts_at)-1),'upcoming');
  assert.equal(roundState({...round,ends_at:'bad'},now),'closed');
  assert.equal(roundState(null,now),'none');
  assert.match(closingLine(null),/No current/);
  assert.match(closingLine(round,now),/Closes.*left/);
});
test('reaction selection supports change and removal; rejects arbitrary emoji',()=>{
  assert.equal(nextReaction(null,'love'),'love');assert.equal(nextReaction('like','fire'),'fire');
  assert.equal(nextReaction('fire','fire'),null);assert.throws(()=>nextReaction(null,'angry'));
});
test('sharing is opt-in and requires an eligible recording and display name',()=>{
  assert.equal(shareState(card,data).action,'share');
  assert.equal(shareState(card,data).label,'Upload to the community board');assert.equal(shareState(card,data).needsName,undefined);
  // A speaker without a name picks one in the same step: still one button.
  const unnamed=shareState(card,{...data,display_name:'player'});
  assert.deepEqual([unnamed.needsName,unnamed.action,unnamed.label],[true,'share','Save name and upload']);
  assert.equal(shareState(card,{...data,display_name:null}).needsName,true);
  assert.equal(shareState({...card,community_round_id:null},data).action,undefined);
  for(const change of [{status:'grading'},{report:{gradable:false}},{audio_url:null},{audio_deleted_at:'today'},{audio_expires_at:new Date(now-1).toISOString()}])assert.equal(shareState({...card,...change},data).action,undefined);
  assert.equal(shareState(card,{...data,round:null}).action,undefined);
  assert.equal(shareState(card,{...data,entry:{speech_id:'other'}}).action,undefined);
});
test('pending and rejected entries can be withdrawn; withdrawal permits only the same speech',()=>{
  const shared={...data,entry:{id:'entry',speech_id:'speech',withdrawn_at:null}};
  assert.equal(shareState(card,shared).action,'withdraw');assert.match(shareState(card,shared).message,/hidden until approved/);
  assert.equal(shareState({...card,moderation_status:'rejected'},shared).action,'withdraw');
  const withdrawn={...shared,entry:{...shared.entry,withdrawn_at:'today'}};
  assert.equal(shareState(card,withdrawn).action,'reshare');assert.equal(shareState(card,withdrawn).label,'Upload again');
  assert.equal(shareState(card,shared).label,'Remove from the community board');
  assert.equal(shareState({...card,moderation_status:'rejected'},withdrawn).action,undefined);
});
function board() {
  const state=createBoardState();state.accept(state.stamp(),{round,entries:[{speech_id:'speech',counts:{love:2},total_reactions:2,my_reaction:'love'}]});return state;
}
test('slow polling cannot erase a newer mutation; counts come from server',async()=>{
  const state=board(), stamp=state.stamp();let release;
  const mutation=state.react('speech','fire',value=>new Promise(resolve=>{assert.equal(value,'fire');release=resolve;}));
  assert.equal(state.accept(stamp,{round,entries:[]}),false);
  assert.equal(await state.react('speech','like',()=>assert.fail('duplicate mutation')),false);
  release({counts:{fire:5},total_reactions:5,my_reaction:'fire'});await mutation;
  assert.equal(state.accept(stamp,{round,entries:[]}),false);
  assert.equal(state.entries[0].total_reactions,5);assert.equal(state.entries[0].my_reaction,'fire');assert.equal(state.pending.size,0);
});
test('failure retains prior reaction and unlocks retry; owners and closed rounds cannot react',async()=>{
  const state=board();await assert.rejects(state.react('speech','love',async value=>{assert.equal(value,null);throw new Error('offline');}));
  assert.equal(state.entries[0].my_reaction,'love');assert.equal(state.entries[0].total_reactions,2);assert.equal(state.pending.size,0);
  state.entries[0].is_owner=true;assert.equal(await state.react('speech','fire',()=>assert.fail()),false);
  state.entries[0].is_owner=false;state.round={...round,ends_at:new Date(now-1).toISOString()};assert.equal(await state.react('speech','fire',()=>assert.fail()),false);
});
test('late reaction response cannot restore entries after round rotation',async()=>{
  const state=board();let release;
  const mutation=state.react('speech','fire',()=>new Promise(resolve=>{release=resolve;}));
  state.invalidate();state.round={...round,id:'next'};state.entries=[];
  release({counts:{fire:1},total_reactions:1,my_reaction:'fire'});await mutation;
  assert.deepEqual(state.entries,[]);
});
