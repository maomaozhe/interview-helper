import test from "node:test";
import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";
import http from "node:http";
import {Agent} from "@earendil-works/pi-agent-core";
import {createModels,fauxProvider,fauxAssistantMessage,fauxToolCall} from "@earendil-works/pi-ai";
import {runQuery} from "../../services/pi-agent/runtime.mjs";

const {input_schema}=JSON.parse(await readFile(new URL("../fixtures/query-tool-contract-before.json",import.meta.url),"utf8"));
const {cases}=JSON.parse(await readFile(new URL("../fixtures/review-state-evidence.json",import.meta.url),"utf8"));

for(const harness of [true,false])for(const scenario of cases)
  test(`Pi review-state answer receives full status totals and bounded details: ${scenario.id} (${harness ? "harness" : "fallback"})`,async t=>{
    const contexts=[],requests=[],rawResults=[];
    const states=structuredClone(scenario.input),records=states.states || states;
    for(const value of Object.values(records))if(value && typeof value==="object" && !Array.isArray(value))
      Object.assign(value,{note:"PRIVATE_REVIEW_NOTE".repeat(2000),raw_question:"PRIVATE_RAW_QUESTION".repeat(2000),
        last_reviewed_at:"PRIVATE_TIMESTAMP",extra_metadata:{raw:"PRIVATE_EXTRA"}});
    const originalStates=structuredClone(states);
    const meta={route:"SQL",...(harness ? {harness:{evidence:[],session:{current_page_ids:["q0"]}}} : {})};
    const subscribe=Agent.prototype.subscribe;
    t.mock.method(Agent.prototype,"subscribe",function(listener){return subscribe.call(this,event=>{
      if(event.type==="tool_execution_end")rawResults.push(structuredClone(event.result));return listener(event);
    });});
    const server=http.createServer(async(req,res)=>{
      let body="";for await(const chunk of req)body+=chunk;requests.push(JSON.parse(body));
      res.writeHead(200,{"Content-Type":"application/json"});res.end(JSON.stringify(requests.length===1 ?
        {answer:"Read requested review states.",facts:{data:[],states,meta}} :
        {answer:"Review suggestions based on the declared status counts.",facts:{data:[],meta:{route:"ANSWER"}}}));
    });
    await new Promise(resolve=>server.listen(0,"127.0.0.1",resolve));
    t.after(()=>new Promise(resolve=>server.close(resolve)));
    const common={filters:Object.fromEntries(Object.entries(input_schema.$defs.FilterSpec.properties)
      .map(([key,value])=>[key,value.default])),sort:"frequency",top_n:null,page_size:20};
    const provider=fauxProvider();provider.setResponses([
      fauxAssistantMessage(fauxToolCall("get_review_state",{...common,action:"REVIEW_STATE",question_ids:["q0"],final:false})),
      fauxAssistantMessage(fauxToolCall("answer_question",{...common,action:"ANSWER",final:true,
        answer_text:"Review suggestions based on the declared status counts.",answer_kind:"STUDY_PLAN",
        answer_basis:"CORPUS",question_ids:[]}))]);
    const models=createModels();models.setProvider(provider.provider);
    const result=await runQuery({run_id:"review-state-evidence",schema:structuredClone(input_schema),
      prompt:"Review facts describe only the requested IDs; detail truncation does not change status totals.",
      context:{message:"Suggest a review plan using their review states.",tool_policy:{
        allowed_actions:{get_review_state:["REVIEW_STATE"],answer_question:["ANSWER"]}}}},
      {hostUrl:`http://127.0.0.1:${server.address().port}`,token:"synthetic-token",streamFn:(_model,context,options)=>{
        contexts.push(structuredClone(context));return models.streamSimple(provider.getModel(),context,options);
      }});
    assert.equal(result.completed,true);assert.equal(result.provider_calls,2);assert.equal(result.tool_calls,2);
    const tool=contexts[1].messages.find(message=>message.role==="toolResult");
    const wire=tool.content.find(block=>block.type==="text").text;
    assert.deepEqual(JSON.parse(wire).states,scenario.expected);
    assert.ok(!wire.includes("PRIVATE"),"notes, raw questions, dates and extra metadata cannot reach the model");
    assert.deepEqual(rawResults[0].structuredContent.facts.states,originalStates,"the full API receipt remains intact");
  });
