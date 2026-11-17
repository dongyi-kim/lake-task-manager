// Collision placement changes y and label positions, never the timestamp's x.
export function layoutTimeAxis(items, range, width, measuredWidths={}) {
  width=Math.max(1,width);
  const markerTracks=[], labelTracks=[[],[]], used=[-8,-8];
  const positioned=items.map(item=>{
    const x=(item.at-range.start)/(range.end-range.start)*width;
    let level=markerTracks.findIndex(previous=>x-previous>=28);
    if(level<0)level=markerTracks.length;
    markerTracks[level]=x;
    const offset=level?(level%2?-1:1)*Math.ceil(level/2)*28:0;
    const result={...item,x,level,offset};
    if(!item.comment) {
      const labelWidth=Math.min(width,measuredWidths[item.uid||item.id]||160);
      let track=-1;
      for(let i=0;i<labelTracks.length;i++)if(used[i]+8+labelWidth<=width&&(track<0||used[i]<used[track]))track=i;
      if(track<0){track=labelTracks.length;labelTracks.push([]);used.push(-8);}
      result.width=labelWidth;result.track=track;
      labelTracks[track].push(result);used[track]+=8+labelWidth;
    }
    return result;
  });
  while(labelTracks.length&&!labelTracks.at(-1).length)labelTracks.pop();
  const above=Math.max(0,...positioned.map(item=>-item.offset)), below=Math.max(0,...positioned.map(item=>item.offset));
  const baseline=12+Math.ceil(labelTracks.length/2)*34+above+20,lowerStart=baseline+below+24;
  for(const item of positioned)item.y=baseline+item.offset;
  labelTracks.forEach((track,index)=>{
    let end=-8;
    for(const item of track){item.left=Math.max(0,item.x-item.width/2,end+8);end=item.left+item.width;}
    let limit=width;
    for(const item of [...track].reverse()){item.left=Math.min(item.left,limit-item.width);limit=item.left-8;}
    for(const item of track){
      item.upper=index%2===0;
      item.labelY=item.upper?12+Math.floor(index/2)*34:lowerStart+Math.floor(index/2)*34;
      item.tipY=item.upper?item.labelY+24:item.labelY;
      item.bendY=item.upper?baseline-above-14:baseline+below+14;
    }
  });
  return {items:positioned,baseline,height:Math.max(78,lowerStart+Math.floor(labelTracks.length/2)*34+12)};
}
