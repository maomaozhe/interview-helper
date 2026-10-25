import test from "node:test";
import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";
import http from "node:http";
import {createModels,fauxProvider,fauxAssistantMessage,fauxToolCall} from "@earendil-works/pi-ai";
import {runQuery} from "../../services/pi-agent/runtime.mjs";

const {input_schema}=JSON.parse(await readFile(new URL("../fixtures/query-tool-contract-before.json",import.meta.url),"utf8"));
for(const harness of [true,false])test(`real Pi evidence keeps complete-workset progress and hides unprocessed records (${harness ? "harness" : "legacy"})`,async t=>{
  const progress={version:"candidate_workset_progress_v1",candidate_total:320,processed:20,unprocessed:300,
    confirmed:8,pending:5,excluded:7,visible:13,batch_size:20,last_batch_count:20,
    processing_complete:false,next_requires_processing:true};
  const meta={route:"SEARCH",candidate_workset_progress:{...progress,candidate_ids:["PRIVATE-INNER-ID"]},
    candidate_workset_snapshot:{raw:["PRIVATE-FULL-RAW"]},engineering_collection_authorization:{raw:"PRIVATE-HISTORY"},
    pagination:{kind:"semantic_candidates",total:320,result_total:13,workset:true,processing_complete:false,
      has_unprocessed:true,next_requires_processing:true,next_cursor:"PRIVATE-CURSOR"}};
  if(harness)meta.harness={evidence:[],session:{has_next_page:true}};
  else meta.pagination.has_unprocessed=1;
  const contexts=[],requests=[];
  const server=http.createServer(async(req,res)=>{
    let body="";for await(const chunk of req)body+=chunk;requests.push(JSON.parse(body));
    res.writeHead(200,{"Content-Type":"application/json"});res.end(JSON.stringify(requests.length===1 ?
      {answer:"20 candidates checked;300 remain.",facts:{data:[],meta}} :
      {answer:"Continue the same scope.",facts:{data:[],meta:{route:"ANSWER"}}}));
  });
  await new Promise(resolve=>server.listen(0,"127.0.0.1",resolve));
  t.after(()=>new Promise(resolve=>server.close(resolve)));
  const common={filters:Object.fromEntries(Object.entries(input_schema.$defs.FilterSpec.properties)
    .map(([key,value])=>[key,value.default])),sort:"frequency",top_n:null,page_size:20};
  const provider=fauxProvider();provider.setResponses([
    fauxAssistantMessage(fauxToolCall("list_questions",{...common,action:"LIST",final:false})),
    fauxAssistantMessage(fauxToolCall("answer_question",{...common,action:"ANSWER",final:true,
      answer_text:"Continue the same scope.",answer_kind:"EXPLAIN",answer_basis:"CORPUS",question_ids:[]}))]);
  const models=createModels();models.setProvider(provider.provider);
  const result=await runQuery({run_id:"workset-evidence",schema:structuredClone(input_schema),prompt:"Use progress honestly.",
    context:{message:"Continue.",tool_policy:{allowed_actions:{list_questions:["LIST"],answer_question:["ANSWER"]}}}},
    {hostUrl:`http://127.0.0.1:${server.address().port}`,token:"fake-token",streamFn:(_model,context,options)=>{
      contexts.push(structuredClone(context));return models.streamSimple(provider.getModel(),context,options);
    }});
  assert.equal(result.completed,true);assert.equal(result.provider_calls,2);
  const tool=contexts[1].messages.find(message=>message.role==="toolResult");
  const wire=tool.content.find(block=>block.type==="text").text,evidence=JSON.parse(wire);
  assert.deepEqual(evidence.meta.candidate_workset_progress,progress);
  assert.equal(evidence.meta.pagination.total,undefined,"candidate universe is not a known business result count");
  assert.equal(evidence.meta.pagination.result_total,13);
  assert.equal(evidence.meta.pagination.has_next_page,true);
  assert.equal(evidence.meta.pagination.next_requires_processing,true);
  assert.equal(evidence.meta.pagination.has_unprocessed,harness ? true : undefined,"new boolean fields cannot be numeric impostors");
  for(const hidden of ["PRIVATE-","candidate_workset_snapshot","engineering_collection_authorization"])
    assert.ok(!wire.includes(hidden),`${hidden} must stay outside the next model request`);
});
