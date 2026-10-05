import test from "node:test";
import assert from "node:assert/strict";
import http from "node:http";
import {createModels, fauxProvider, fauxAssistantMessage, fauxToolCall} from "@earendil-works/pi-ai";
import {runQuery} from "../../services/pi-agent/runtime.mjs";

async function host(t) {
  const received=[];
  const server=http.createServer(async(req,res)=>{
    let body="";for await(const chunk of req)body+=chunk;
    received.push({url:req.url,auth:req.headers.authorization,plan:JSON.parse(body)});
    res.writeHead(200,{"Content-Type":"application/json"});
    res.end(JSON.stringify({answer:"40 rows",facts:{meta:{route:"SQL"},data:Array.from({length:40},(_,i)=>({canonical_question_id:String(i)}))}}));
  });
  await new Promise(resolve=>server.listen(0,"127.0.0.1",resolve));
  t.after(()=>new Promise(resolve=>server.close(resolve)));
  return {url:`http://127.0.0.1:${server.address().port}`,received};
}
const plan={action:"LIST",filters:{coding_focus:"ALGORITHM"},top_n:40,page_size:40};
const schema={type:"object",properties:{action:{type:"string"},filters:{type:"object"},
  top_n:{type:"integer"},page_size:{type:"integer"}},required:["action","filters","top_n","page_size"]};

function stream(responses){
  const faux=fauxProvider();
  faux.setResponses(responses);
  const models=createModels();models.setProvider(faux.provider);
  return {faux,fn:(_model,context,options)=>models.streamSimple(faux.getModel(),context,options)};
}

test("actual pinned Pi executes one domain tool and stops with all host rows retained",async t=>{
  const h=await host(t), fake=stream([fauxAssistantMessage(fauxToolCall("list_questions",plan))]);
  const result=await runQuery({run_id:"test-run",conversation_id:"session",schema,prompt:"Use the tool.",context:{message:"前40个算法题"}},
    {hostUrl:h.url,token:"internal-only",streamFn:fake.fn});
  assert.equal(result.provider_calls,1);assert.equal(result.tool_calls,1);
  assert.equal(h.received[0].url,"/internal/agent/runs/test-run/tools/list_questions");
  assert.equal(h.received[0].auth,"Bearer internal-only");assert.equal(h.received[0].plan.top_n,40);
  assert.ok(result.events.some(e=>e.type==="agent_end"));
  assert.equal(fake.faux.state.callCount,1);
});

test("multiple terminal tools are rejected before any host mutation",async t=>{
  const h=await host(t), fake=stream([fauxAssistantMessage([
    fauxToolCall("list_questions",plan),fauxToolCall("record_review",{...plan,action:"RECORD_REVIEW"})])]);
  await assert.rejects(runQuery({run_id:"test",schema,prompt:"Use a tool",context:{message:"list"}},
    {hostUrl:h.url,token:"internal",streamFn:fake.fn}),/QUERY_PLAN_MISSING|No more faux/);
  assert.equal(h.received.length,0);
});

test("aborted sessions cannot invoke a domain tool",async t=>{
  const h=await host(t), fake=stream([fauxAssistantMessage(fauxToolCall("list_questions",plan))]);
  const controller=new AbortController();controller.abort();
  await assert.rejects(runQuery({run_id:"test",schema,prompt:"Use a tool",context:{message:"list"}},
    {hostUrl:h.url,token:"internal",signal:controller.signal,streamFn:fake.fn}),/abort/i);
  assert.equal(h.received.length,0);
});

test("an incomplete first plan is repaired before the host receives a query",async t=>{
  const h=await host(t),fake=stream([
    fauxAssistantMessage(fauxToolCall("list_questions",{action:"LIST"})),
    fauxAssistantMessage(fauxToolCall("list_questions",plan)),
  ]);
  const result=await runQuery({run_id:"test",schema,prompt:"Provide complete parameters",context:{message:"只看二面"}},
    {hostUrl:h.url,token:"internal",streamFn:fake.fn});
  assert.equal(result.provider_calls,2);
  assert.equal(h.received.length,1);
  assert.equal(h.received[0].plan.top_n,40);
});

test("pinned Pi carries completed reads into the next step and a terminal write",async t=>{
  const h=await host(t),fake=stream([
    fauxAssistantMessage(fauxToolCall("get_question_stats",{...plan,action:"STATS",final:false})),
    fauxAssistantMessage(fauxToolCall("list_questions",{...plan,final:false})),
    fauxAssistantMessage(fauxToolCall("record_review",{...plan,action:"RECORD_REVIEW",final:true})),
  ]);
  const result=await runQuery({run_id:"multi",schema,prompt:"Read, list, then record",context:{message:"统计后列出并标记"}},
    {hostUrl:h.url,token:"internal",streamFn:fake.fn});
  assert.equal(result.provider_calls,3);assert.equal(result.tool_calls,3);
  assert.deepEqual(h.received.map(r=>r.plan.action),["STATS","LIST","RECORD_REVIEW"]);
  assert.ok(result.events.filter(e=>e.type==="tool_execution_end").length===3);
});
