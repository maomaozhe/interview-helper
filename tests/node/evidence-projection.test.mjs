import test from "node:test";
import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";
import http from "node:http";
import {Agent} from "@earendil-works/pi-agent-core";
import {createModels,fauxProvider,fauxAssistantMessage,fauxToolCall} from "@earendil-works/pi-ai";
import {runQuery} from "../../services/pi-agent/runtime.mjs";

const {input_schema}=JSON.parse(await readFile(new URL("../fixtures/query-tool-contract-before.json",import.meta.url),"utf8"));
// Actual second-turn model content captured before projection consolidation.
// Keep the text strings intact: property order and UTF-16 slicing are behavior.
const before=JSON.parse(await readFile(new URL("../fixtures/evidence-projection-before.json",import.meta.url),"utf8"));

for(const scenario of before.cases)test(`Pi evidence projection remains frozen: ${scenario.id}`,async t=>{
  const requests=[],contexts=[],rawResults=[];
  const subscribe=Agent.prototype.subscribe;
  // The public runtime event callback omits result; observe the raw Pi event to
  // verify structuredContent without replacing execution or the real listener.
  t.mock.method(Agent.prototype,"subscribe",function(listener){
    return subscribe.call(this,event=>{
      if(event.type==="tool_execution_end")rawResults.push(JSON.parse(JSON.stringify(event.result)));
      return listener(event);
    });
  });
  const server=http.createServer(async(req,res)=>{
    let body="";for await(const chunk of req)body+=chunk;
    requests.push({url:req.url,authorization:req.headers.authorization,plan:JSON.parse(body)});
    res.writeHead(200,{"Content-Type":"application/json"});
    res.end(JSON.stringify(requests.length===1 ? scenario.host_result : {
      answer:"Synthetic final answer",facts:{data:[],meta:{route:"ANSWER"}}}));
  });
  await new Promise(resolve=>server.listen(0,"127.0.0.1",resolve));
  t.after(()=>new Promise(resolve=>server.close(resolve)));
  const common={filters:Object.fromEntries(Object.entries(input_schema.$defs.FilterSpec.properties).map(([k,v])=>[k,v.default])),
    sort:"frequency",top_n:null,page_size:20};
  const plans=[{...common,action:"DETAILS",question_ids:["q0"],final:false},
    {...common,action:"ANSWER",answer_text:"Synthetic final answer",answer_kind:"EXPLAIN",
      answer_basis:"CORPUS",question_ids:[],final:true}];
  const fake=fauxProvider();fake.setResponses([
    fauxAssistantMessage(fauxToolCall("get_question_details",plans[0])),
    fauxAssistantMessage(fauxToolCall("answer_question",plans[1]))]);
  const models=createModels();models.setProvider(fake.provider);

  const result=await runQuery({run_id:"evidence-freeze",schema:structuredClone(input_schema),prompt:"Read then explain.",
    context:{message:"Explain synthetic questions.",tool_policy:{allowed_actions:{get_question_details:["DETAILS"],answer_question:["ANSWER"]}}}},
    {hostUrl:`http://127.0.0.1:${server.address().port}`,token:"synthetic-token",streamFn:(_m,c,o)=>{
      contexts.push(JSON.parse(JSON.stringify(c)));return models.streamSimple(fake.getModel(),c,o);
    }});

  assert.equal(result.completed,true);
  assert.equal(result.provider_calls,2);
  assert.equal(result.tool_calls,2);
  assert.deepEqual(requests.map(request=>request.plan),plans);
  assert.ok(requests.every(request=>request.authorization==="Bearer synthetic-token"));
  assert.equal(rawResults.length,2);
  assert.deepEqual(rawResults[0].structuredContent,scenario.host_result,"complete host receipt must survive projection");
  const message=contexts[1].messages.find(message=>message.role==="toolResult");
  assert.ok(message,"the second actual model turn must receive the first tool result");
  const expectedContent=structuredClone(scenario.expected_content);
  const expectedSummary=JSON.parse(expectedContent[0].text);
  if(Object.hasOwn(expectedSummary,"states")){
    // The old fixture deliberately retains the complete historical receipt.
    // Only review-state model evidence changes: notes and dates no longer pass.
    if(expectedSummary.states===null)delete expectedSummary.states;
    else {
      const hasState=Object.hasOwn(expectedSummary.states,"q0");
      expectedSummary.states={scope:"requested_question_ids",items:hasState ? [{canonical_question_id:"q0",status:"WEAK",
        review_count:1,version:1,binding_status:"RESOLVED",last_score:2}] : [],total_count:hasState ? 1 : 0,
        valid_count:hasState ? 1 : 0,invalid_count:0,returned_count:hasState ? 1 : 0,truncated:false,
        status_counts:{UNSEEN:0,WEAK:hasState ? 1 : 0,REVIEWED:0,MASTERED:0},summary_complete:true};
    }
    expectedContent[0].text=JSON.stringify(expectedSummary);
  }
  assert.deepEqual(message.content,expectedContent,"all other raw summary fields remain frozen");
  const text=message.content.find(block=>block.type==="text").text;
  for(const marker of ["SYNTHETIC_SIGNED_CURSOR","SYNTHETIC_RESULT_SET","SYNTHETIC_SOURCE_BODY","SYNTHETIC_SOURCE_PATH"])
    assert.ok(!text.includes(marker),`${marker} must remain outside model evidence`);
  assert.ok(!text.includes("Synthetic review note"),"private review notes stay in the complete receipt");
  if(scenario.id==="missing-harness-fallback-all-fields-and-utf16-limits") {
    const summary=JSON.parse(text);
    assert.equal(summary.items[0].canonical_text.length,400);
    assert.equal(summary.items[0].canonical_text.charCodeAt(399),0xd83d);
    assert.equal(summary.items[0].sources[0].quote.length,160);
    assert.equal(summary.items[0].sources[0].quote.charCodeAt(159),0xd83d);
  }
});
