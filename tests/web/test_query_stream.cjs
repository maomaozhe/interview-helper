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

test('timeout closes the stream and cancellation cannot become a success',async()=>{
  let cancelled=0;const pending=watch({timeoutMs:5,cancel:()=>cancelled++}),source=Source.latest;
  await assert.rejects(pending,/查询超时/);assert.equal(cancelled,1);assert.equal(source.closed,true);
});
