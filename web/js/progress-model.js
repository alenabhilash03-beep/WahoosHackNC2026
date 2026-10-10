import { AXES, firstAndBest } from './radar.js';

export function progressFor(me, motionId = '') {
  const speeches = (me.speeches ?? []).filter(s => (!motionId || s.motion_id === motionId)
    && s.status === 'done' && s.factor_scores && Number.isFinite(s.overall?.score))
    .sort((a,b)=>String(a.created_at).localeCompare(String(b.created_at)) || String(a.id).localeCompare(String(b.id)));
  const reports = speeches.map(s=>({gradable:true,factors:Object.fromEntries(AXES.map(a=>[a.key,{score:s.factor_scores[a.key]}]))}));
  const summary = firstAndBest(reports);
  const best = speeches.reduce((best,s)=>!best || s.overall.score > best.overall.score ? s : best,null);
  const gains = summary ? AXES.map(a=>({...a,gain:summary.best[a.key] == null || summary.first[a.key] == null ? null : summary.best[a.key]-summary.first[a.key]}))
    .sort((a,b)=>(b.gain ?? -Infinity)-(a.gain ?? -Infinity)) : [];
  return {summary,best,gains,recent:speeches.slice(-10),speeches};
}

export function sparklinePoints(scores,width=360,height=90) {
  const values=scores.filter(Number.isFinite);
  return values.map((v,i)=>[values.length===1 ? width/2 : 4+i*(width-8)/(values.length-1),
    4+(1-Math.max(0,Math.min(100,v))/100)*(height-8)]);
}
