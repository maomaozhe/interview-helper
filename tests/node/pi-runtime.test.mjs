import test from "node:test";
import assert from "node:assert/strict";
import http from "node:http";
import {createModels, fauxProvider, fauxAssistantMessage, fauxToolCall} from "@earendil-works/pi-ai";
import {runQuery} from "../../services/pi-agent/runtime.mjs";

async function host(t, responseFor) {
  const received=[];
  const server=http.createServer(async(req,res)=>{
    let body="";for await(const chunk of req)body+=chunk;
    received.push({url:req.url,auth:req.headers.authorization,plan:JSON.parse(body)});
    res.writeHead(200,{"Content-Type":"application/json"});
    const result={answer:"40 rows",facts:{meta:{route:"SQL"},data:Array.from({length:40},(_,i)=>({canonical_question_id:String(i)}))}};
    res.end(JSON.stringify(responseFor ? responseFor(JSON.parse(body),result) : result));
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

function visibleTools(context) {
  const names=new Set();
  for(const message of context.messages.filter(m=>m.role==="system")) {
    for(const removed of message.toolsRemoved || []) names.delete(typeof removed==="string" ? removed : removed.name);
    for(const added of message.toolsAdded || []) names.add(added.name);
  }
  return [...names];
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

test("read-only capabilities exclude a forged write before any host request",async t=>{
  const h=await host(t),fake=stream([fauxAssistantMessage(fauxToolCall("record_review",{...plan,action:"RECORD_REVIEW"}))]);
  const visible=[];
  await assert.rejects(runQuery({run_id:"guard",schema,prompt:"Read only",context:{message:"list",tool_policy:{
    allowed_actions:{list_questions:["LIST","CLARIFY"],get_question_stats:["STATS"],search_questions:["SEARCH"]}}}},
    {hostUrl:h.url,token:"internal",streamFn:(m,c,o)=>{visible.push(visibleTools(c));return fake.fn(m,c,o);}}),
    /QUERY_PLAN_MISSING|No more faux|not found|UNKNOWN_TOOL/i);
  assert.equal(h.received.length,0);
  assert.ok(visible.length>0);
  assert.ok(visible.every(names=>!names.includes("record_review")&&!names.includes("get_question_details")));
});

test("host refresh makes a write available only after the requested page is displayed",async t=>{
  const h=await host(t,(args,result)=>({...result,planning:{spec:{final:args.final}},facts:{...result.facts,meta:{
    route:"SQL",harness:{session:{current_page_ids:["0","1","2"]},tool_policy:{allowed_actions:
      args.final===false ? {list_questions:["LIST"],record_review:["RECORD_REVIEW"]} : {}}}}}}));
  const fake=stream([
    fauxAssistantMessage(fauxToolCall("list_questions",{...plan,final:false})),
    fauxAssistantMessage(fauxToolCall("record_review",{...plan,action:"RECORD_REVIEW",final:true})),
  ]);
  const visible=[];
  const result=await runQuery({run_id:"refresh",schema,prompt:"List then save",context:{message:"列出并标记为已掌握",
    tool_policy:{allowed_actions:{list_questions:["LIST"]}}}},
    {hostUrl:h.url,token:"internal",streamFn:(m,c,o)=>{visible.push(visibleTools(c));return fake.fn(m,c,o);}});
  assert.equal(result.provider_calls,2);
  assert.deepEqual(visible[0],["list_questions"]);
  assert.ok(visible[1].includes("record_review"));
  assert.deepEqual(h.received.map(r=>r.plan.action),["LIST","RECORD_REVIEW"]);
});

test("search tool schema preserves optional bounded lexical facets and forwards them unchanged",async t=>{
  const h=await host(t);
  const lexicalFacets=["Agent 上下文维护 设计","Agent memory 记忆管理 设计","Agent 工具执行恢复 权限 设计"];
  const searchPlan={action:"SEARCH",filters:{},top_n:null,page_size:15,
    search_query:"Agent harness 设计",relevance_query:"Agent运行框架设计",pipeline:"HYBRID_RERANK",
    lexical_facets:lexicalFacets};
  const searchSchema={...schema,additionalProperties:false,properties:{...schema.properties,
    top_n:{anyOf:[{type:"integer"},{type:"null"}]},search_query:{type:"string"},
    relevance_query:{type:"string"},pipeline:{type:"string"},
    lexical_facets:{type:"array",maxItems:3,items:{type:"string",minLength:1,maxLength:150}}}};
  const fake=stream([fauxAssistantMessage(fauxToolCall("search_questions",searchPlan))]);
  const result=await runQuery({run_id:"facets",schema:searchSchema,prompt:"Search parallel concepts",
    context:{message:"harness设计题",tool_policy:{allowed_actions:{search_questions:["SEARCH"]}}}},
    {hostUrl:h.url,token:"internal",streamFn:fake.fn});
  assert.equal(result.provider_calls,1);
  assert.equal(h.received.length,1);
  assert.deepEqual(h.received[0].plan.lexical_facets,lexicalFacets);
  assert.equal(h.received[0].plan.relevance_query,"Agent运行框架设计");
});

test("search count constraints reject global top_n and oversized pages before host execution",async t=>{
  const h=await host(t);
  const valid={action:"SEARCH",filters:{},top_n:null,page_size:15,search_query:"Agent设计",pipeline:"HYBRID_RERANK"};
  const searchSchema={...schema,properties:{...schema.properties,
    top_n:{anyOf:[{type:"integer"},{type:"null"}]},
    page_size:{type:"integer",minimum:1,maximum:100},search_query:{type:"string"},pipeline:{type:"string"}}};
  const fake=stream([
    fauxAssistantMessage(fauxToolCall("search_questions",{...valid,top_n:15})),
    fauxAssistantMessage(fauxToolCall("search_questions",{...valid,page_size:51})),
    fauxAssistantMessage(fauxToolCall("search_questions",valid)),
  ]);
  const result=await runQuery({run_id:"search-count",schema:searchSchema,prompt:"Return fifteen matching questions",
    context:{message:"Agent设计题，返回15道",tool_policy:{allowed_actions:{search_questions:["SEARCH"]}}}},
    {hostUrl:h.url,token:"internal",streamFn:fake.fn});
  assert.equal(result.provider_calls,3);
  assert.equal(h.received.length,1);
  assert.deepEqual(h.received[0].plan,valid);
  // The shared input schema and LIST's global top_n remain valid for other tools.
  assert.equal(searchSchema.properties.page_size.maximum,100);
  assert.equal(searchSchema.properties.top_n.anyOf[0].type,"integer");
});
