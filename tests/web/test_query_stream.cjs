const test=require('node:test');
const assert=require('node:assert/strict');
const {watchQueryRun}=require('../../src/interview_intelligence/web/assets/query-stream.js');

class Source {
  static latest;
  constructor(url){this.url=String(url);this.listeners={};this.closed=false;Source.latest=this;}
  addEventListener(type,fn){this.listeners[type]=fn;}
  emit(type,data,sequence){this.listeners[type]?.({data:JSON.stringify(data),lastEventId:String(sequence)});}
  close(){this.closed=true;}
}
const snapshot={status:'RUNNING',event_cursor:0,last_sequence:1,events:[{sequence:1,type:'accepted',data:{run_id:'r1'}}]};
function watch(options={}){return watchQueryRun({url:'http://localhost/api/runs/r1/events',snapshot,EventSourceClass:Source,
  readRun:async()=>{throw Error('unexpected poll');},pollMs:1,...options});}

test('SSE replays once and ends only with the checked completion',async()=>{
  const updates=[];const pending=watch({onEvent:e=>updates.push(e)}),source=Source.latest;
  assert.match(source.url,/after=1/);
  source.emit('stage',{stage:'planning'},2);
  source.emit('stage',{stage:'planning'},2);
  source.emit('clarification_delta',{text:'哪个方向',temporary:true},3);
  source.emit('model_request',{messages:['private']},4);
  source.emit('completed',{result:{answer:'哪个方向？'}},5);
  source.emit('stage',{stage:'reranking'},6);
  assert.deepEqual(await pending,{answer:'哪个方向？'});
  assert.deepEqual(updates.map(e=>e.type),['accepted','stage','clarification_delta']);
  assert.equal(source.closed,true);
});

test('disconnect drains journal pages for the same run without resubmission',async()=>{
  const cursors=[],updates=[];
  const pending=watch({onEvent:e=>updates.push(e),readRun:async cursor=>{
    cursors.push(cursor);
    if(cursor===2)return {status:'SUCCEEDED',last_sequence:5,events:[{sequence:3,type:'stage',data:{stage:'reranking'}}]};
    assert.equal(cursor,3);
    return {status:'SUCCEEDED',last_sequence:5,events:[{sequence:3,type:'stage',data:{stage:'reranking'}},
      {sequence:4,type:'tool_result',data:{}},{sequence:5,type:'completed',data:{result:{run_id:'r1'}}}]};
  }});
  Source.latest.emit('stage',{stage:'planning'},2);Source.latest.onerror();
  assert.deepEqual(await pending,{run_id:'r1'});
  assert.deepEqual(cursors,[2,3]);
  assert.equal(updates.filter(e=>e.stage==='reranking').length,1);
  assert.equal(updates.filter(e=>e.type==='connection').length,1);
});

test('persisted failure returns its partial result after SSE disconnect',async()=>{
  const partial={answer:'已完成的统计'};
  const pending=watch({readRun:async()=>({status:'FAILED',last_sequence:2,error_code:'QUERY_DEADLINE_EXCEEDED',
    partial_result:partial,events:[{sequence:2,type:'failed',data:{code:'QUERY_DEADLINE_EXCEEDED'}}]})});
  Source.latest.onerror();
  await assert.rejects(pending,error=>error.message==='QUERY_DEADLINE_EXCEEDED' && error.partial===partial);
});

test('abort cancels exactly once and ignores later stream completion',async()=>{
  const controller=new AbortController();let cancelled=0;
  const pending=watch({signal:controller.signal,cancel:()=>cancelled++}),source=Source.latest;
  controller.abort();source.emit('completed',{result:{answer:'too late'}},2);
  await assert.rejects(pending,{name:'AbortError'});
  assert.equal(cancelled,1);assert.equal(source.closed,true);
});

test('already completed snapshot does not open another event stream',async()=>{
  let opened=0;
  const result=await watch({snapshot:{status:'SUCCEEDED',event_cursor:0,last_sequence:0,events:[],result:{answer:'已完成'}},
    EventSourceClass:class {constructor(){opened++;}}});
  assert.equal(result.answer,'已完成');assert.equal(opened,0);
});

test('silent SSE discovers persisted success before the watchdog, despite one transient status error',async()=>{
  let checks=0,cancelled=0,guard;
  const result={answer:'事件流未送达，但结果已保存'};
  const pending=watch({timeoutMs:1000,statusPollMs:5,cancel:()=>cancelled++,readRun:async()=>{
    if(++checks===1)throw Error('temporary status error');
    return {status:'SUCCEEDED',last_sequence:1,result,events:[]};
  }}),source=Source.latest;
  try{
    assert.deepEqual(await Promise.race([pending,new Promise((_,reject)=>{
      guard=setTimeout(()=>reject(Error('status check waited for watchdog')),250);
    })]),result);
  }finally{clearTimeout(guard);}
  assert.equal(checks,2);assert.equal(cancelled,0);assert.equal(source.closed,true);
});

test('status checks and disconnect recovery do not read the journal concurrently',async()=>{
  let releaseFirst,startedFirst,active=0,maxActive=0,checks=0;
  const firstStarted=new Promise(resolve=>{startedFirst=resolve;});
  const firstRead=new Promise(resolve=>{releaseFirst=resolve;});
  const result={answer:'recovered'};
  const pending=watch({timeoutMs:1000,statusPollMs:5,readRun:async()=>{
    checks++;active++;maxActive=Math.max(maxActive,active);
    try{
      if(checks===1){startedFirst();await firstRead;
        return {status:'RUNNING',last_sequence:1,events:[]};}
      return {status:'SUCCEEDED',last_sequence:1,result,events:[]};
    }finally{active--;}
  }}),source=Source.latest;
  await firstStarted;
  source.onerror();releaseFirst();
  assert.deepEqual(await pending,result);
  assert.equal(maxActive,1);assert.equal(checks,2);
});

test('a late status check cannot replay completion after SSE has resolved',async()=>{
  let releaseRead,startedRead,cancelled=0;
  const readStarted=new Promise(resolve=>{startedRead=resolve;});
  const readGate=new Promise(resolve=>{releaseRead=resolve;});
  const updates=[];const result={answer:'stream result'};
  const pending=watch({timeoutMs:1000,statusPollMs:5,cancel:()=>cancelled++,
    onEvent:event=>updates.push(event.type),readRun:async()=>{
      startedRead();await readGate;
      return {status:'SUCCEEDED',last_sequence:3,result,events:[
        {sequence:2,type:'stage',data:{stage:'assembling'}},
        {sequence:3,type:'completed',data:{result}}]};
    }}),source=Source.latest;
  await readStarted;
  source.emit('completed',{result},2);releaseRead();
  assert.deepEqual(await pending,result);
  await Promise.resolve();
  assert.deepEqual(updates,['accepted']);assert.equal(cancelled,0);
});

test('watchdog can recover success after an earlier status poll hangs',async()=>{
  let checks=0,cancelled=0;
  const result={answer:'persisted after stalled poll'};
  const pending=watch({timeoutMs:35,statusPollMs:5,statusReadTimeoutMs:10,
    cancel:()=>cancelled++,readRun:async()=>{
      if(++checks===1)return new Promise(()=>{});
      return {status:'SUCCEEDED',last_sequence:1,result,events:[]};
    }}),source=Source.latest;
  assert.deepEqual(await pending,result);
  assert.equal(checks,2);assert.equal(cancelled,0);assert.equal(source.closed,true);
});

test('hanging status reads cannot leave the watchdog pending forever',async()=>{
  let checks=0,cancelled=0,guard;
  const pending=watch({timeoutMs:35,statusPollMs:5,statusReadTimeoutMs:10,
    cancel:()=>cancelled++,readRun:async()=>{
      checks++;return new Promise(()=>{});
    }}),source=Source.latest;
  try{
    await assert.rejects(Promise.race([pending,new Promise((_,reject)=>{
      guard=setTimeout(()=>reject(Error('watchdog remained blocked')),250);
    })]),error=>error.code==='RUN_STATUS_TIMEOUT');
  }finally{clearTimeout(guard);}
  assert.equal(checks,2);assert.equal(cancelled,0);assert.equal(source.closed,true);
});

test('watchdog reads a persisted success even when SSE completion never arrives',async()=>{
  let cancelled=0;const cursors=[];
  const result={answer:'已保存的结果'};
  const pending=watch({timeoutMs:5,cancel:()=>cancelled++,readRun:async cursor=>{
    cursors.push(cursor);
    if(cursor===1)return {status:'SUCCEEDED',last_sequence:3,result,events:[
      {sequence:2,type:'stage',data:{stage:'assembling'}}]};
    assert.equal(cursor,2);
    return {status:'SUCCEEDED',last_sequence:3,result,events:[
      {sequence:3,type:'completed',data:{result}}]};
  }}),source=Source.latest;
  assert.deepEqual(await pending,result);
  assert.deepEqual(cursors,[1,2]);
  assert.equal(cancelled,0);assert.equal(source.closed,true);
});

test('watchdog returns a persisted failure instead of cancelling it',async()=>{
  let cancelled=0;const partial={answer:'已完成的部分'};
  const pending=watch({timeoutMs:5,cancel:()=>cancelled++,readRun:async()=>({
    status:'FAILED',last_sequence:2,error_code:'QUERY_DEADLINE_EXCEEDED',partial_result:partial,
    events:[{sequence:2,type:'failed',data:{code:'QUERY_DEADLINE_EXCEEDED'}}]
  })});
  await assert.rejects(pending,error=>error.message==='QUERY_DEADLINE_EXCEEDED' && error.partial===partial);
  assert.equal(cancelled,0);
});

test('watchdog cancels a run that is still running',async()=>{
  let cancelled=0;const cursors=[];
  const pending=watch({timeoutMs:5,cancel:()=>cancelled++,readRun:async cursor=>{
    cursors.push(cursor);return {status:'RUNNING',last_sequence:cursor,events:[]};
  }}),source=Source.latest;
  await assert.rejects(pending,/查询超时/);
  assert.deepEqual(cursors,[1]);assert.equal(cancelled,1);assert.equal(source.closed,true);
});

test('watchdog does not cancel a run when its final status cannot be read',async()=>{
  let cancelled=0;
  const pending=watch({timeoutMs:5,cancel:()=>cancelled++,readRun:async()=>{
    throw Error('status unavailable');
  }}),source=Source.latest;
  await assert.rejects(pending,/status unavailable/);
  assert.equal(cancelled,0);assert.equal(source.closed,true);
});

test('failed complete-scope query keeps its code and explains why no complete list is available',async()=>{
  const pending=watch({timeoutMs:1000,statusPollMs:5,readRun:async()=>({
    status:'FAILED',last_sequence:1,error_code:'SEMANTIC_SCOPE_INCOMPLETE',events:[]
  })});
  await assert.rejects(pending,error=>error.code==='SEMANTIC_SCOPE_INCOMPLETE'
    && /全量算法核验尚未完成/.test(error.message));
});
