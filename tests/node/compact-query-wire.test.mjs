import test from "node:test";
import assert from "node:assert/strict";
import http from "node:http";
import {readFile} from "node:fs/promises";
import {Compile} from "typebox/compile";
import {runQuery} from "../../services/pi-agent/runtime.mjs";

// The immutable fixture contains the real Python query_model_schema captured at
// the pinned Pi boundary. Add the explicit v16 fields without regenerating or
// reducing the original filter/schema fixture.
const frozen=JSON.parse(await readFile(new URL("../fixtures/query-tool-contract-before.json",import.meta.url),"utf8"));
const schema=structuredClone(frozen.input_schema);
schema.properties.user_goal={type:"object",additionalProperties:false,properties:{
  objective:{type:"string",minLength:1,maxLength:800},
  response_mode:{type:"string",enum:["DIRECT_RESULT","SYNTHESIS"]}},required:["objective","response_mode"]};
schema.properties.evidence_tool_ordinals={type:"array",maxItems:8,items:{type:"integer",minimum:1,maximum:8}};
schema.required.push("user_goal");
schema.allOf=[
  {if:{properties:{action:{not:{enum:["LIST","NEXT","COUNT","STATS"]}}},required:["action"]},
    then:{properties:{semantic_task:{type:"null"}}}},
  {if:{properties:{action:{const:"SEARCH"}},required:["action"]},then:{properties:{
    top_n:{type:"null"},page_size:{type:"integer",minimum:1,maximum:50},
    search_query:{type:"string",minLength:1,maxLength:500}},required:["search_query"]}},
];
const goal={objective:"核查当前公司算法结果并解释覆盖范围",response_mode:"SYNTHESIS"};
const filters=Object.fromEntries(Object.entries(schema.$defs.FilterSpec.properties).map(([key,value])=>[key,value.default]));
filters.company="小红书";
const common={filters,sort:"frequency",top_n:null,page_size:20,user_goal:goal};
const count={...common,action:"COUNT",final:true};
const search={...common,action:"SEARCH",final:true,search_query:"算法题",pipeline:"HYBRID_RERANK"};
const answer={...common,action:"ANSWER",final:true,answer_text:"目前确认 4 道，候选检索不能证明全库只有 4 道。",
  answer_kind:"EXPLAIN",answer_basis:"CORPUS",question_ids:[],evidence_tool_ordinals:[1,2,3]};
const domain={COUNT:"get_question_count",SEARCH:"search_questions",ANSWER:"answer_question",LIST:"list_questions"};
const initialPolicy={allowed_actions:{list_questions:["LIST","NEXT","CLARIFY"],search_questions:["SEARCH"],
  get_question_count:["COUNT"],get_question_details:["DETAILS"],answer_question:["ANSWER"]}};

async function fixtureHost(t,{steps,reply}={}) {
  const modelRequests=[],hostRequests=[];
  const server=http.createServer(async(req,res)=>{
    let body="";for await(const chunk of req)body+=chunk;
    const payload=JSON.parse(body);
    if(req.url.endsWith("/v1/chat/completions")) {
      modelRequests.push(payload);
      const planned=steps[modelRequests.length-1];
      assert.ok(planned,"the runtime must not silently open an extra model turn");
      const calls=(Array.isArray(planned) ? planned : [planned]).map((step,index)=>({
        index,id:`call-${modelRequests.length}-${index}`,type:"function",
        function:{name:step.name,arguments:JSON.stringify(step.args)}}));
      res.writeHead(200,{"Content-Type":"text/event-stream"});
      res.write(`data: ${JSON.stringify({id:"synthetic",object:"chat.completion.chunk",created:0,model:"query",choices:[
        {index:0,delta:{role:"assistant",tool_calls:calls},finish_reason:null}]})}\n\n`);
      res.write(`data: ${JSON.stringify({id:"synthetic",object:"chat.completion.chunk",created:0,model:"query",choices:[
        {index:0,delta:{},finish_reason:"tool_calls"}],usage:{prompt_tokens:100,completion_tokens:50,total_tokens:150}})}\n\n`);
      res.end("data: [DONE]\n\n");
      return;
    }
    hostRequests.push({url:req.url,plan:payload});
    const ordinal=hostRequests.length,terminal=payload.action==="ANSWER" || goal.response_mode!==payload.user_goal.response_mode;
    const receipt={answer:payload.answer_text || "本步读取完成",planning:{spec:payload},facts:{data:[],
      counts:{canonical_questions:ordinal===2 ? 4 : 80,occurrences:ordinal===2 ? 5 : 81},meta:{route:"SQL",
        applied_filters:filters,completion:{terminal,tool_ordinal:ordinal,status:terminal ? "COMPLETE" : "NEEDS_RESPONSE"},
        user_goal:payload.user_goal,harness:{user_goal:payload.user_goal,session:{current_page_ids:[]}}}}};
    const result=reply ? reply(payload,receipt,ordinal) : receipt;
    res.writeHead(200,{"Content-Type":"application/json"});res.end(JSON.stringify(result));
  });
  await new Promise(resolve=>server.listen(0,"127.0.0.1",resolve));
  t.after(()=>new Promise(resolve=>server.close(resolve)));
  return {modelRequests,hostRequests,url:`http://127.0.0.1:${server.address().port}`};
}

function query(h,{wire="query_step_v1",policy=initialPolicy,max=4}={}) {
  return runQuery({run_id:"compact-wire",schema,prompt:"Preserve the user goal and explain the evidence.",
    ...(wire===undefined ? {} : {tool_wire_version:wire}),max_model_calls:max,
    context:{message:"核查算法题覆盖范围",tool_policy:policy,user_goal:goal}},
  {hostUrl:h.url,token:"synthetic-local-only"});
}
function step(args,name="query_step") {return {name,args};}
function withoutActionEnum(parameters) {
  const copy=structuredClone(parameters);delete copy.properties.action.enum;return copy;
}

test("compact wire uses the pinned OpenAI serializer and keeps multiple reads, evidence and host completion",async t=>{
  const policies=[
    {allowed_actions:{search_questions:["SEARCH"],get_question_count:["COUNT"],answer_question:["ANSWER"]}},
    {allowed_actions:{get_question_count:["COUNT"],answer_question:["ANSWER"]}},
    {allowed_actions:{answer_question:["ANSWER"]}},
  ];
  const h=await fixtureHost(t,{steps:[step(count),step(search),step(count),step(answer)],reply:(plan,result,ordinal)=>{
    if(policies[ordinal-1])result.facts.meta.harness.tool_policy=policies[ordinal-1];
    if(plan.action==="SEARCH")Object.assign(result.facts.meta,{candidate_count:50,eligible_count:80,returned_count:4,
      result_completeness:{scope:"retrieval_candidates",candidate_count:50,eligible_count:80,returned_count:4,
        corpus_complete:false,verification_complete:true}});
    return result;
  }});
  const result=await query(h);
  assert.equal(result.completed,true);assert.equal(result.provider_calls,4);assert.equal(result.tool_calls,4);
  assert.deepEqual(h.hostRequests.map(row=>row.url.split("/").at(-1)),[
    "get_question_count","search_questions","get_question_count","answer_question"]);
  assert.ok(h.hostRequests.every(row=>JSON.stringify(row.plan.user_goal)===JSON.stringify(goal)));
  assert.deepEqual(h.hostRequests.at(-1).plan.evidence_tool_ordinals,[1,2,3]);
  const parameters=h.modelRequests.map(request=>{
    assert.equal(request.tools.length,1);assert.equal(request.tools[0].function.name,"query_step");
    return request.tools[0].function.parameters;
  });
  assert.deepEqual(parameters.map(value=>value.properties.action.enum),[
    ["LIST","NEXT","CLARIFY","SEARCH","DETAILS","COUNT","ANSWER"],
    ["SEARCH","COUNT","ANSWER"],["COUNT","ANSWER"],["ANSWER"]]);
  assert.ok(parameters.every(value=>JSON.stringify(withoutActionEnum(value))===JSON.stringify(withoutActionEnum(parameters[0]))),
    "policy refresh changes only the compact action enum");
  const last=h.modelRequests.at(-1),evidence=last.messages.filter(message=>message.role==="tool")
    .map(message=>JSON.parse(message.content));
  assert.deepEqual(evidence.map(row=>row.tool_ordinal),[1,2,3]);
  assert.ok(evidence.every(row=>row.user_goal.objective===goal.objective && row.completion.terminal===false));
  assert.equal(evidence[1].meta.result_completeness.corpus_complete,false);
  assert.equal(evidence[1].meta.result_completeness.verification_complete,true);
  assert.equal(evidence[1].meta.eligible_count,80);assert.equal(evidence[1].counts.occurrences,5);
  assert.match(h.modelRequests[0].messages[0].content,/Wire query_step_v1/);
  assert.deepEqual(result.events.filter(event=>event.type==="tool_execution_start").map(event=>event.toolName),[
    "get_question_count","search_questions","get_question_count","answer_question"]);
  assert.ok(result.events.filter(event=>event.type==="tool_execution_start").every(event=>event.wireToolName==="query_step"));
  assert.ok(result.events.some(event=>event.type==="message_end" && event.message?.role==="assistant" &&
    event.message.content.some(block=>block.type==="toolCall" && block.name==="query_step")),
    "provider assistant messages preserve the original wire call as evidence");
});

test("serialized compact tool bytes fall substantially with the same real filter-schema fixture",async t=>{
  const direct={...count,user_goal:{...goal,response_mode:"DIRECT_RESULT"}};
  const compact=await fixtureHost(t,{steps:[step(direct)]});await query(compact,{max:1});
  const legacy=await fixtureHost(t,{steps:[step(direct,"get_question_count")]});
  await assert.rejects(query(legacy,{wire:null,max:1}),/INVALID_TOOL_WIRE_VERSION/);
  assert.equal(legacy.modelRequests.length,0);
  // An absent version is the historical menu; null/unknown versions are errors.
  await runQuery({run_id:"legacy-wire",schema,prompt:"Use a domain tool.",max_model_calls:1,
    context:{message:"有多少题",tool_policy:initialPolicy}}, {hostUrl:legacy.url,token:"synthetic-local-only"});
  const compactBytes=Buffer.byteLength(JSON.stringify(compact.modelRequests[0].tools));
  const legacyBytes=Buffer.byteLength(JSON.stringify(legacy.modelRequests[0].tools));
  t.diagnostic(`serialized tools: legacy=${legacyBytes} bytes compact=${compactBytes} bytes ratio=${(compactBytes/legacyBytes).toFixed(3)}`);
  assert.ok(compactBytes<legacyBytes*0.6,`${compactBytes} must be below 60% of ${legacyBytes}`);
  assert.equal(legacy.modelRequests[0].tools.length,5);
  assert.equal(compact.modelRequests[0].tools.length,1);
  assert.equal(legacy.modelRequests[0].messages[0].content,"Use a domain tool.");
  assert.deepEqual(compact.modelRequests[0].tools[0].function.parameters.allOf.slice(0,schema.allOf.length),schema.allOf);
});

for(const [label,call] of [
  ["unknown action",step({...count,action:"INVENTED"})],
  ["unauthorized mutation",step({...count,action:"RECORD_REVIEW",review_items:[{canonical_question_id:"q1",status:"MASTERED"}]})],
  ["forged domain tool name",step(count,"get_question_count")],
  ["invented tool name",step(count,"invented_tool")],
  ["multiple calls",[step(count),step(count)]],
])test(`compact wire blocks ${label} before any host execution`,async t=>{
  const h=await fixtureHost(t,{steps:[call]});
  await assert.rejects(query(h,{max:1}),/QUERY_MODEL_BUDGET_EXCEEDED|QUERY_PLAN_MISSING/);
  assert.equal(h.hostRequests.length,0);
});

test("a policy contraction rejects an action exposed on an earlier turn",async t=>{
  const h=await fixtureHost(t,{steps:[step(count),step(search)],reply:(_plan,result)=>{
    result.facts.meta.harness.tool_policy={allowed_actions:{answer_question:["ANSWER"]}};return result;
  }});
  await assert.rejects(query(h,{max:2}),/QUERY_MODEL_BUDGET_EXCEEDED|QUERY_PLAN_MISSING/);
  assert.deepEqual(h.hostRequests.map(row=>row.plan.action),["COUNT"]);
  assert.deepEqual(h.modelRequests[1].tools[0].function.parameters.properties.action.enum,["ANSWER"]);
});

test("compact validation preserves active action constraints and all host semantic conditions",async t=>{
  const direct={...count,user_goal:{...goal,response_mode:"DIRECT_RESULT"}};
  const legacy=await fixtureHost(t,{steps:[step(direct,"get_question_count")]});
  await runQuery({run_id:"schema-legacy",schema,prompt:"Count.",max_model_calls:1,
    context:{message:"多少题",tool_policy:initialPolicy}}, {hostUrl:legacy.url,token:"synthetic-local-only"});
  const compact=await fixtureHost(t,{steps:[step(direct)]});await query(compact,{max:1});
  const compactParameters=compact.modelRequests[0].tools[0].function.parameters;
  const compactCheck=Compile(compactParameters);
  const legacyTools=new Map(legacy.modelRequests[0].tools.map(tool=>[tool.function.name,tool.function]));
  const cases=[
    count,{...count,top_n:2},{...count,user_goal:null},
    {...count,user_goal:undefined},
    search,{...search,page_size:51},{...search,top_n:2},{...search,search_query:""},
    {...search,semantic_task:"ALGORITHM_PROBLEM"},
    {...answer,evidence_tool_ordinals:[1]}, {...answer,final:false},
    {...answer,question_ids:Array.from({length:11},(_,i)=>String(i))},
    {...common,action:"LIST",semantic_task:"ALGORITHM_PROBLEM"},
    {...common,action:"LIST",page_size:101},
  ];
  for(const candidate of cases) {
    const plan=JSON.parse(JSON.stringify(candidate));
    const old=Compile(legacyTools.get(domain[plan.action]).parameters).Check(plan);
    assert.equal(compactCheck.Check(plan),old,JSON.stringify(plan));
  }
  assert.ok(compactParameters.required.includes("user_goal"));
  assert.equal(compactParameters.properties.user_goal.additionalProperties,false);
  assert.deepEqual(compactParameters.allOf.slice(0,schema.allOf.length),schema.allOf);
});

test("complete QuerySpec neutral defaults remain valid for SEARCH and ANSWER",async t=>{
  const defaults=Object.fromEntries(Object.entries(schema.properties)
    .filter(([,value])=>Object.hasOwn(value,"default")).map(([key,value])=>[key,value.default]));
  const completeSearch={...defaults,...search,semantic_task:null,evidence_tool_ordinals:[]};
  const completeAnswer={...defaults,...answer,semantic_task:null,evidence_tool_ordinals:[1]};
  const h=await fixtureHost(t,{steps:[step(completeSearch),step(completeAnswer)]});
  const result=await query(h,{max:2});
  assert.equal(result.completed,true);
  assert.deepEqual(h.hostRequests.map(row=>row.plan),[completeSearch,completeAnswer]);
  const parameters=h.modelRequests[0].tools[0].function.parameters;
  assert.equal(parameters.additionalProperties,false);
  assert.equal(Compile(parameters).Check({...completeSearch,invented_scope:"unbounded"}),false);
  assert.equal(Compile(parameters).Check({...completeSearch,action:"RECORD_REVIEW"}),false);
  // Compact wire intentionally accepts complete QuerySpec defaults. Historical
  // domain-tool menus reject these fields because they declare a smaller shape;
  // the host QuerySpec has always accepted the neutral fields themselves.
  assert.equal(completeSearch.semantic_task,null);
  assert.equal(completeAnswer.pipeline,"HYBRID_RERANK");
});
