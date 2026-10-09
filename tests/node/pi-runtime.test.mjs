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

test("soft question type preference survives the actual Pi tool schema and host boundary",async t=>{
  const h=await host(t);
  const searchPlan={action:"SEARCH",filters:{},top_n:null,page_size:20,
    search_query:"如何设计一个系统",pipeline:"HYBRID_RERANK",preferred_question_type:"SYSTEM_DESIGN"};
  const searchSchema={...schema,additionalProperties:false,properties:{...schema.properties,
    top_n:{type:"null"},search_query:{type:"string"},pipeline:{type:"string"},
    preferred_question_type:{anyOf:[{type:"string",enum:["SYSTEM_DESIGN","CODE"]},{type:"null"}]}}};
  const fake=stream([fauxAssistantMessage(fauxToolCall("search_questions",searchPlan))]);
  const schemas=[];
  const result=await runQuery({run_id:"soft-type",schema:searchSchema,prompt:"Use the optional soft preference",
    context:{message:"工程系统设计题",tool_policy:{allowed_actions:{search_questions:["SEARCH"]}}}},
    {hostUrl:h.url,token:"internal",streamFn:(m,c,o)=>{
      for(const message of c.messages.filter(x=>x.role==="system"))
        for(const tool of message.toolsAdded || []) if(tool.name==="search_questions") schemas.push(tool.parameters);
      return fake.fn(m,c,o);
    }});
  assert.equal(result.provider_calls,1);
  assert.equal(h.received.length,1);
  assert.equal(h.received[0].plan.preferred_question_type,"SYSTEM_DESIGN");
  assert.deepEqual(h.received[0].plan.filters,{});
  assert.ok(schemas.length>0);
  assert.deepEqual(schemas[0].properties.preferred_question_type,searchSchema.properties.preferred_question_type);
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

const answerSchema={...schema,additionalProperties:false,properties:{...schema.properties,
  top_n:{anyOf:[{type:"integer"},{type:"null"}]},
  review_statuses:{type:"array",maxItems:4,items:{type:"string",enum:["UNSEEN","WEAK","REVIEWED","MASTERED"]}},
  final:{type:"boolean"},question_ids:{type:"array",maxItems:100,items:{type:"string"}},
  answer_text:{type:"string",minLength:1,maxLength:12000},
  answer_kind:{type:"string",enum:["EXPLAIN","COMPARE","SOLVE","STUDY_PLAN","CHAT"]},
  answer_basis:{type:"string",enum:["GENERAL_KNOWLEDGE","CORPUS","MIXED"]}}};
const answerPlan={...plan,action:"ANSWER",top_n:null,final:true,
  answer_text:"Redis 的速度主要来自内存访问及高效的数据结构。\n这是参考解释。",
  answer_kind:"EXPLAIN",answer_basis:"MIXED",question_ids:["q1"]};

test("pinned Pi reads detail evidence before a final answer and preserves answer fields",async t=>{
  const detail="Redis为什么快？来源中的原始提问。";
  const h=await host(t,(args,result)=>args.action==="DETAILS" ? {...result,
    planning:{spec:{final:false}},facts:{data:[{canonical_question_id:"q1",canonical_text:detail}],
      meta:{route:"SQL",harness:{session:{current_page_ids:["q1"]},
        tool_policy:{allowed_actions:{answer_question:["ANSWER"]}}}}}} : {...result,
    answer:args.answer_text,planning:{spec:{final:true}},facts:{data:[],meta:{route:"ANSWER"}}});
  const fake=stream([
    fauxAssistantMessage(fauxToolCall("get_question_details",{...plan,action:"DETAILS",question_ids:["q1"],final:false})),
    fauxAssistantMessage(fauxToolCall("answer_question",answerPlan)),
  ]),contexts=[];
  const result=await runQuery({run_id:"explain",schema:answerSchema,prompt:"Read the question, then explain it.",
    context:{message:"解释第一题",tool_policy:{allowed_actions:{get_question_details:["DETAILS"]}}}},
    {hostUrl:h.url,token:"internal",streamFn:(m,c,o)=>{contexts.push(c);return fake.fn(m,c,o);}});
  assert.equal(result.completed,true);assert.equal(result.provider_calls,2);assert.equal(result.tool_calls,2);
  assert.deepEqual(h.received.map(row=>row.plan.action),["DETAILS","ANSWER"]);
  assert.deepEqual(h.received[1].plan,answerPlan);
  assert.ok(JSON.stringify(contexts[1].messages).includes(detail));
  assert.deepEqual(visibleTools(contexts[1]),["answer_question"]);
});

test("count evidence covers the full scope and can feed a subsequent answer",async t=>{
  const counts={canonical_questions:2452,occurrences:2771,interviews:185,source_documents:172,known_companies:20};
  const h=await host(t,(args,result)=>({...result,planning:{spec:{final:args.final}},
    facts:{data:[],counts,meta:{route:"SQL",count_scope:"full_corpus"}}}));
  const countPlan={...plan,action:"COUNT",top_n:null,final:false,review_statuses:["UNSEEN","WEAK","REVIEWED"]};
  const finalPlan={...answerPlan,answer_text:"题库有 2452 道去重题目和 2771 次提问记录。",question_ids:[],answer_basis:"CORPUS"};
  const fake=stream([
    fauxAssistantMessage(fauxToolCall("get_question_count",countPlan)),
    fauxAssistantMessage(fauxToolCall("answer_question",finalPlan)),
  ]),contexts=[];
  const result=await runQuery({run_id:"count-answer",schema:answerSchema,prompt:"Count then explain the units.",
    context:{message:"有多少数据？解释一下数量口径",tool_policy:{allowed_actions:{get_question_count:["COUNT"],answer_question:["ANSWER"]}}}},
    {hostUrl:h.url,token:"internal",streamFn:(m,c,o)=>{contexts.push(c);return fake.fn(m,c,o);}});
  assert.equal(result.provider_calls,2);assert.equal(result.tool_calls,2);
  assert.deepEqual(h.received.map(row=>row.plan.action),["COUNT","ANSWER"]);
  assert.deepEqual(h.received[0].plan.review_statuses,["UNSEEN","WEAK","REVIEWED"]);
  const declarations=contexts[0].messages.filter(message=>message.role==="system")
    .flatMap(message=>message.toolsAdded || []);
  assert.deepEqual(declarations.find(tool=>tool.name==="get_question_count").parameters.properties.top_n,{type:"null"});
  assert.match(JSON.stringify(contexts[1].messages),/canonical_questions.*2452/);
  assert.deepEqual(h.received[1].plan,finalPlan);
});

for(const hostProjection of [false,true])test(`large sources and signed cursors stay outside model evidence (${hostProjection ? 'host projection' : 'fallback'})`,async t=>{
  const cursor="SIGNED_CURSOR_PRIVATE_"+"x".repeat(10000),large="unbounded source material ".repeat(1000);
  const rows=Array.from({length:20},(_,i)=>({canonical_question_id:`q${i}`,canonical_text:"R".repeat(1000),
    occurrence_count:50,sources:Array.from({length:10},()=>({revision_id:"r1",start_line:5,end_line:6,
      quote:large,raw_markdown:large,file_path:"PRIVATE_PATH"})),raw_markdown:large}));
  const h=await host(t,(args,result)=>({...result,planning:{spec:{final:args.final}},facts:{data:rows,meta:{route:"SQL",
    result_set_id:cursor,arbitrary_metadata:large,
    pagination:{total:2452,result_total:2452,returned:20,page_size:20,next_cursor:cursor,private_note:large},
    harness:{session:{current_page_ids:["q0"],has_next_page:true},
      ...(hostProjection ? {evidence:[{canonical_question_id:"q0",canonical_text:"HOST_PROJECTED_QUESTION"}]} : {}),
      tool_policy:{allowed_actions:{answer_question:["ANSWER"]}}}}}}));
  const fake=stream([
    fauxAssistantMessage(fauxToolCall("get_question_details",{...plan,action:"DETAILS",question_ids:["q0"],final:false})),
    fauxAssistantMessage(fauxToolCall("answer_question",{...answerPlan,question_ids:["q0"]})),
  ]),contexts=[];
  await runQuery({run_id:"bounded-evidence",schema:answerSchema,prompt:"Read then explain.",
    context:{message:"解释第一题",tool_policy:{allowed_actions:{get_question_details:["DETAILS"]}}}},
    {hostUrl:h.url,token:"internal",streamFn:(m,c,o)=>{contexts.push(c);return fake.fn(m,c,o);}});
  const toolMessage=contexts[1].messages.find(message=>message.role==="toolResult"),
    text=toolMessage.content.find(block=>block.type==="text").text,summary=JSON.parse(text);
  assert.ok(text.length<5000);assert.ok(!text.includes(cursor));assert.ok(!text.includes(large));
  assert.ok(!text.includes("PRIVATE_PATH"));assert.equal(summary.meta.harness,undefined);
  assert.equal(summary.meta.result_set_id,undefined);assert.equal(summary.meta.arbitrary_metadata,undefined);
  assert.equal(summary.meta.pagination.has_next_page,true);
  assert.equal(summary.meta.pagination.total,2452);assert.equal(summary.meta.pagination.next_cursor,undefined);
  if(hostProjection)assert.deepEqual(summary.items,[{canonical_question_id:"q0",canonical_text:"HOST_PROJECTED_QUESTION"}]);
  else {
    assert.equal(summary.items.length,5);assert.equal(summary.items[0].canonical_text.length,400);
    assert.equal(summary.items[0].sources.length,2);assert.equal(summary.items[0].sources[0].quote.length,160);
    assert.equal(summary.items[0].sources[0].raw_markdown,undefined);
  }
});

test("topic statistics preserve L2 grouping through the host and subsequent model evidence",async t=>{
  const statsSchema={...answerSchema,properties:{...answerSchema.properties,
    group_by:{type:"string",enum:["question","topic","company","round"]},
    topic_level:{type:"string",enum:["L1","L2"]}}};
  const statsPlan={...plan,action:"STATS",filters:{topic_l1:"Redis"},top_n:null,page_size:20,
    group_by:"topic",topic_level:"L2",final:false};
  const h=await host(t,(args,result)=>({...result,planning:{spec:{final:args.final}},facts:{
    data:[{key:"redis.persistence",occurrence_count:8}],meta:{route:"SQL",group_by:"topic",topic_level:"L2",
      applied_filters:{topic_l1:"Redis"},corpus_revision:293,sample_counts:{occurrences:42},total_groups:5}}}));
  const fake=stream([
    fauxAssistantMessage(fauxToolCall("get_question_stats",statsPlan)),
    fauxAssistantMessage(fauxToolCall("answer_question",{...answerPlan,question_ids:[],answer_kind:"STUDY_PLAN"})),
  ]),contexts=[];
  const result=await runQuery({run_id:"topic-breakdown",schema:statsSchema,prompt:"Read the Redis topic distribution, then make a study plan.",
    context:{message:"按Redis知识点制定复习计划",tool_policy:{allowed_actions:{get_question_stats:["STATS"],answer_question:["ANSWER"]}}}},
    {hostUrl:h.url,token:"internal",streamFn:(m,c,o)=>{contexts.push(c);return fake.fn(m,c,o);}});
  assert.equal(result.provider_calls,2);assert.equal(result.tool_calls,2);
  assert.deepEqual(h.received[0].plan,statsPlan);
  const message=contexts[1].messages.find(row=>row.role==="toolResult"),
    summary=JSON.parse(message.content.find(block=>block.type==="text").text);
  assert.equal(summary.meta.topic_level,"L2");assert.equal(summary.meta.group_by,"topic");
  assert.equal(summary.meta.total_groups,5);assert.equal(summary.meta.sample_counts.occurrences,42);
  assert.deepEqual(summary.meta.applied_filters,{topic_l1:"Redis"});
});

test("answer must terminate and cite at most ten questions before any host execution",async t=>{
  const h=await host(t),fake=stream([
    fauxAssistantMessage(fauxToolCall("answer_question",{...answerPlan,final:false})),
    fauxAssistantMessage(fauxToolCall("answer_question",{...answerPlan,question_ids:Array.from({length:11},(_,i)=>`q${i}`)})),
    fauxAssistantMessage(fauxToolCall("answer_question",answerPlan)),
  ]);
  const result=await runQuery({run_id:"answer-guard",schema:answerSchema,prompt:"Return a bounded final answer.",
    context:{message:"解释这些题",tool_policy:{allowed_actions:{answer_question:["ANSWER"]}}}},
    {hostUrl:h.url,token:"internal",streamFn:fake.fn});
  assert.equal(result.provider_calls,3);assert.equal(h.received.length,1);
  assert.deepEqual(h.received[0].plan,answerPlan);
});
