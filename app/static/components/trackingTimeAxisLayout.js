import { compressTimeAxis } from './trackingTimeAxisDensity.js';

// Collision placement changes y, never an individual event's timestamp x.
export function layoutTimeAxis(items,range,width,measuredWidths={}) {
  width=Math.max(1,width);
  const compressed=compressTimeAxis(items,range,width), baseline=96;
  const ends=[-Infinity,-Infinity,-Infinity];
  const positioned=compressed.items.map(item=>{
    const x=(item.at-range.start)/(range.end-range.start)*width;
    const level=ends.findIndex(end=>x-end>=48);
    ends[level]=x;
    const offset=[0,-28,28][level];
    return {...item,x,level,offset,y:baseline+offset,showCallout:false};
  });
  const budget=Math.min(6,Math.max(2,2*Math.floor(width/280))), rows=[[],[]];
  const priority={done:0,reopen:0,progress:1,new:2,mixed:3,change:4};
  const candidates=positioned.filter(item=>!item.comment);
  const chosen=[], sourceUse=new Map();
  while(chosen.length<budget&&candidates.length) {
    candidates.sort((a,b)=>{
      const rank=(priority[a.tone]??4)-(priority[b.tone]??4);
      if(rank)return rank;
      const source=(sourceUse.get(a.issueKey)||0)-(sourceUse.get(b.issueKey)||0);
      if(source)return source;
      const distance=item=>chosen.length?Math.min(...chosen.map(other=>Math.abs(item.x-other.x))):width;
      return distance(b)-distance(a)||a.x-b.x;
    });
    const item=candidates.shift();
    const labelWidth=Math.min(width,240,measuredWidths[item.uid]||measuredWidths[item.id]||160);
    const available=rows.map(row=>row.reduce((sum,other)=>sum+other.width,0)+row.length*8+labelWidth<=width);
    let track=rows[0].length<=rows[1].length?0:1;
    if(!available[track])track=1-track;
    if(!available[track])continue;
    Object.assign(item,{showCallout:true,width:labelWidth,track,upper:track===0});
    rows[track].push(item);chosen.push(item);
    sourceUse.set(item.issueKey,(sourceUse.get(item.issueKey)||0)+1);
  }
  for(let track=0;track<2;track++) {
    const row=rows[track].sort((a,b)=>a.x-b.x);
    let end=-8;
    for(const item of row){item.left=Math.max(0,item.x-item.width/2,end+8);end=item.left+item.width;}
    let limit=width;
    for(const item of [...row].reverse()){item.left=Math.min(item.left,limit-item.width);limit=item.left-8;}
    for(const item of row)Object.assign(item,{labelY:track===0?12:156,
      tipY:track===0?36:156,bendY:track===0?50:142});
  }
  return {...compressed,items:positioned,baseline:positioned.length?baseline:38,
    height:positioned.length?192:78,shownCalloutCount:chosen.length,
    hiddenCalloutCount:positioned.length-chosen.length};
}
