import test from "node:test";
import assert from "node:assert/strict";
import http from "node:http";
import {createModels,fauxProvider,fauxAssistantMessage,fauxToolCall} from "@earendil-works/pi-ai";
import {runQuery} from "../../services/pi-agent/runtime.mjs";

const goal={objective:"核查当前结果的覆盖范围并说明结论",response_mode:"SYNTHESIS"};
const schema={type:"object",additionalProperties:false,properties:{
  action:{type:"string"},filters:{type:"object"},sort:{type:"string"},
  top_n:{anyOf:[{type:"integer"},{type:"null"}]},page_size:{type:"integer"},final:{type:"boolean"},
  user_goal:{type:"object",additionalProperties:false,properties:{objective:{type:"string",maxLength:800},
    response_mode:{type:"string",enum:["DIRECT_RESULT","SYNTHESIS"]}},required:["objective","response_mode"]},
  question_ids:{type:"array",items:{type:"string"}},answer_text:{type:"string"},
  answer_kind:{type:"string"},answer_basis:{type:"string"},
  evidence_tool_ordinals:{type:"array",items:{type:"integer",minimum:1}},
  search_query:{type:"string"},pipeline:{type:"string"}},
  required:["action","filters","sort","top_n","page_size","user_goal"]};
const common={filters:{company:"小红书"},sort:"frequency",top_n:null,page_size:20,user_goal:goal};
const plans={
  count:{...common,action:"COUNT",final:true},
  list:{...common,action:"LIST",final:true},
  details:{...common,action:"DETAILS",question_ids:["q1"],final:true},
  answer:{...common,action:"ANSWER",question_ids:[],final:true,answer_text:"目前确认4道，现有证据不能确认只有4道。",
    answer_kind:"EXPLAIN",answer_basis:"CORPUS",evidence_tool_ordinals:[1,2,3]},
};
const tools={COUNT:"get_question_count",LIST:"list_questions",DETAILS:"get_question_details",ANSWER:"answer_question"};

function stream(planned){
  const provider=fauxProvider();provider.setResponses(planned.map(plan=>fauxAssistantMessage(fauxToolCall(tools[plan.action],plan))));
  const models=createModels();models.setProvider(provider.provider);
  return {provider,fn:(_model,context,options)=>models.streamSimple(provider.getModel(),context,options)};
}

async function host(t,respond){
  const received=[];
  const server=http.createServer(async(request,response)=>{
    let body="";for await(const chunk of request)body+=chunk;
    received.push(JSON.parse(body));
    response.writeHead(200,{"Content-Type":"application/json"});
    response.end(JSON.stringify(respond(received.at(-1),received.length)));
  });
  await new Promise(resolve=>server.listen(0,"127.0.0.1",resolve));
  t.after(()=>new Promise(resolve=>server.close(resolve)));
  return {received,url:`http://127.0.0.1:${server.address().port}`};
}

function receipt(plan,ordinal,{terminal=plan.action==="ANSWER",completion,policy,budget}={}){
  const user_goal={version:"user_goal_v1",source:"MODEL_PLAN",run_id:"goal-runtime",objective:goal.objective,
    response_mode:goal.response_mode};
  return {answer:plan.answer_text || "已执行本步取证。",planning:{spec:{...plan,final:plan.final}},facts:{
    data:plan.action==="LIST" || plan.action==="DETAILS" ? [{canonical_question_id:"q1",canonical_text:"已核验来源题目"}] : [],
    meta:{route:plan.action==="ANSWER" ? "ANSWER" : "SQL",user_goal,
      completion:completion===undefined ? {status:terminal ? "COMPLETE" : "NEEDS_RESPONSE",terminal,
        proposed_final:plan.final,tool_ordinal:ordinal} : completion,
      harness:{user_goal,session:{current_page_ids:["q1"]},...(policy ? {tool_policy:policy} : {}),
        ...(budget ? {budget} : {})}}}};
}

test("the response turn sees the host reserve and only affordable tools",async t=>{
  const budget={remaining_shared_calls:2,pending_planning_calls:1,completion_reserve_calls:1,search_model_calls:3};
  const policy={allowed_actions:{answer_question:["ANSWER"],list_questions:["CLARIFY"]},model_budget:budget};
  const h=await host(t,(plan,ordinal)=>receipt(plan,ordinal,{policy,budget}));
  const fake=stream([plans.count,{...plans.answer,evidence_tool_ordinals:[1]}]),contexts=[];
  const result=await runQuery({run_id:"reserve",schema,prompt:"Use current evidence when reads cannot fit.",
    max_model_calls:4,context:{message:"分析覆盖"}},
    {hostUrl:h.url,token:"synthetic",streamFn:(model,context,options)=>{
      contexts.push(structuredClone(context));return fake.fn(model,context,options);
    }});
  assert.equal(result.completed,true);assert.equal(h.received.length,2);
  const menu=new Map();
  for(const message of contexts[1].messages.filter(message=>message.role==="system")) {
    for(const tool of message.toolsRemoved || []) menu.delete(typeof tool==="string" ? tool : tool.name);
    for(const tool of message.toolsAdded || []) menu.set(tool.name,tool);
  }
  assert.deepEqual([...menu.keys()].sort(),["answer_question","list_questions"]);
  const evidence=contexts[1].messages.find(message=>message.role==="toolResult");
  assert.deepEqual(JSON.parse(evidence.content.find(block=>block.type==="text").text).budget,budget);
});

test("host completion keeps three reads nonterminal and carries their evidence into the fourth planning turn",async t=>{
  const h=await host(t,(plan,ordinal)=>receipt(plan,ordinal));
  const fake=stream([plans.count,plans.list,plans.details,plans.answer]),contexts=[];
  const result=await runQuery({run_id:"goal-runtime",schema,prompt:"Keep the user goal, then answer from tool receipts.",
    max_model_calls:4,context:{message:"核查这些结果够不够完整，并解释依据",user_goal:goal}},
    {hostUrl:h.url,token:"synthetic",streamFn:(model,context,options)=>{
      contexts.push(structuredClone(context));return fake.fn(model,context,options);
    }});
  assert.equal(result.completed,true);assert.equal(result.provider_calls,4);assert.equal(result.tool_calls,4);
  assert.deepEqual(h.received.map(plan=>plan.action),["COUNT","LIST","DETAILS","ANSWER"]);
  assert.ok(h.received.slice(0,3).every(plan=>plan.final===true),"host can override an early final proposal");
  assert.deepEqual(h.received.at(-1).evidence_tool_ordinals,[1,2,3]);
  assert.ok(h.received.every(plan=>JSON.stringify(plan.user_goal)===JSON.stringify(goal)));
  const evidence=contexts.at(-1).messages.filter(message=>message.role==="toolResult").map(message=>
    JSON.parse(message.content.find(block=>block.type==="text").text));
  assert.deepEqual(evidence.map(row=>row.tool_ordinal),[1,2,3]);
  assert.ok(evidence.every(row=>row.completion.terminal===false));
  assert.ok(evidence.every(row=>row.user_goal.objective===goal.objective));
});

test("eight planning turns can gather evidence and answer without the old six-call clamp",async t=>{
  const h=await host(t,(plan,ordinal)=>receipt(plan,ordinal));
  const fake=stream([...Array.from({length:7},()=>({...plans.count,final:false})),plans.answer]);
  const result=await runQuery({run_id:"complex-budget",schema,prompt:"Gather evidence, then explain.",
    max_model_calls:8,context:{message:"综合核查并给出复习建议",user_goal:goal}},
    {hostUrl:h.url,token:"synthetic",streamFn:fake.fn});
  assert.equal(result.completed,true);
  assert.equal(result.provider_calls,8);
  assert.equal(h.received.length,8);
  assert.equal(h.received.at(-1).action,"ANSWER");
});

test("an out-of-range planning budget is rejected before any provider or tool call",async t=>{
  const h=await host(t,(plan,ordinal)=>receipt(plan,ordinal));
  const fake=stream([plans.answer]);
  await assert.rejects(runQuery({run_id:"invalid-budget",schema,prompt:"Answer.",max_model_calls:33,
    context:{message:"解释"}},{hostUrl:h.url,token:"synthetic",streamFn:fake.fn}),/INVALID_MODEL_BUDGET/);
  assert.equal(fake.provider.state.callCount,0);
  assert.equal(h.received.length,0);
});

test("host terminal completion ends a direct result even when the plan proposed final=false",async t=>{
  const plan={...plans.list,final:false,user_goal:{objective:"列出本公司题目",response_mode:"DIRECT_RESULT"}};
  const h=await host(t,(received,ordinal)=>receipt(received,ordinal,{terminal:true})),fake=stream([plan]);
  const result=await runQuery({run_id:"host-final",schema,prompt:"List the requested questions.",max_model_calls:4,
    context:{message:"列出这些题"}},{hostUrl:h.url,token:"synthetic",streamFn:fake.fn});
  assert.equal(result.completed,true);assert.equal(result.provider_calls,1);assert.equal(h.received.length,1);
});

test("a host supplied planning budget exhausts without accepting a nonterminal result",async t=>{
  const h=await host(t,(plan,ordinal)=>receipt(plan,ordinal,{terminal:false}));
  const fake=stream([plans.count,plans.list,plans.answer]);
  await assert.rejects(runQuery({run_id:"exhausted",schema,prompt:"Read and explain.",max_model_calls:2,
    context:{message:"分析覆盖范围"}},{hostUrl:h.url,token:"synthetic",streamFn:fake.fn}),/QUERY_MODEL_BUDGET_EXCEEDED/);
  assert.equal(fake.provider.state.callCount,2);assert.equal(h.received.length,2);
});

test("remaining planning budget is honored without opening a new budget",async t=>{
  const h=await host(t,(plan,ordinal)=>receipt(plan,ordinal,{terminal:false}));
  const fake=stream([plans.count,plans.answer]);
  await assert.rejects(runQuery({run_id:"remaining",schema,prompt:"Read and explain.",
    context:{message:"继续核查",budget:{remaining_model_calls:1}}},
    {hostUrl:h.url,token:"synthetic",streamFn:fake.fn}),/QUERY_MODEL_BUDGET_EXCEEDED/);
  assert.equal(fake.provider.state.callCount,1);assert.equal(h.received.length,1);
});

test("zero remaining planning calls prevents every provider and tool call",async t=>{
  const h=await host(t,(plan,ordinal)=>receipt(plan,ordinal)),fake=stream([plans.answer]);
  await assert.rejects(runQuery({run_id:"zero",schema,prompt:"Answer.",max_model_calls:0,context:{message:"继续"}},
    {hostUrl:h.url,token:"synthetic",streamFn:fake.fn}),/QUERY_MODEL_BUDGET_EXCEEDED/);
  assert.equal(fake.provider.state.callCount,0);assert.equal(h.received.length,0);
});

for(const completion of [null,{terminal:"true"},{status:"COMPLETE"}])
  test(`malformed host completion never falls back to final=true: ${JSON.stringify(completion)}`,async t=>{
    const h=await host(t,(plan,ordinal)=>receipt(plan,ordinal,{completion})),fake=stream([plans.count]);
    await assert.rejects(runQuery({run_id:"invalid-completion",schema,prompt:"Read.",max_model_calls:1,
      context:{message:"核查"}},{hostUrl:h.url,token:"synthetic",streamFn:fake.fn}),/INVALID_HOST_COMPLETION/);
    assert.equal(h.received.length,1);
  });

test("a refreshed host policy can keep every mutation unavailable during continued reasoning",async t=>{
  const policy={allowed_actions:{answer_question:["ANSWER"]}};
  const h=await host(t,(plan,ordinal)=>receipt(plan,ordinal,{policy}));
  const provider=fauxProvider();provider.setResponses([
    fauxAssistantMessage(fauxToolCall("get_question_count",plans.count)),
    fauxAssistantMessage(fauxToolCall("record_review",{...common,action:"RECORD_REVIEW",final:true,
      review_items:[{canonical_question_id:"q1",status:"MASTERED"}]})),
  ]);
  const models=createModels();models.setProvider(provider.provider);
  await assert.rejects(runQuery({run_id:"read-only-multi",schema,prompt:"Explain without saving.",max_model_calls:2,
    context:{message:"核查题目数量，不要保存复习状态",tool_policy:{allowed_actions:{get_question_count:["COUNT"]}}}},
    {hostUrl:h.url,token:"synthetic",streamFn:(_model,context,options)=>models.streamSimple(provider.getModel(),context,options)}),
    /QUERY_MODEL_BUDGET_EXCEEDED|QUERY_PLAN_MISSING|not found|UNKNOWN_TOOL/i);
  assert.deepEqual(h.received.map(plan=>plan.action),["COUNT"]);
});
