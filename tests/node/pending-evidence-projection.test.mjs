import test from "node:test";
import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";
import http from "node:http";
import {createModels,fauxProvider,fauxAssistantMessage,fauxToolCall} from "@earendil-works/pi-ai";
import {runQuery} from "../../services/pi-agent/runtime.mjs";

const {input_schema}=JSON.parse(await readFile(new URL("../fixtures/query-tool-contract-before.json",import.meta.url),"utf8"));
const rows=[{canonical_question_id:"confirmed",canonical_text:"confirmed question",occurrence_count:3,
  semantic_verification:"CONFIRMED",confirmed_occurrence_count:3,pending_occurrence_count:1},
  {canonical_question_id:"pending",canonical_text:"pending question",occurrence_count:0,
    semantic_verification:"PENDING",confirmed_occurrence_count:0,pending_occurrence_count:2}];
const semanticScope={task:"ENGINEERING_DESIGN",complete:false,accepted:3,pending:3,unresolved:2,missing:1};
const semanticCounts={confirmed_canonical_questions:1,pending_only_canonical_questions:1,
  pending_canonical_questions:2,selected_canonical_questions:2,confirmed_occurrences:3,
  pending_occurrences:3,selected_occurrences:6};
const pendingCounts={canonical_questions:2,occurrences:3};

for(const useHarness of [true,false])test(`actual Pi next turn retains pending evidence with ${useHarness ? "host harness" : "legacy fallback"}`,async t=>{
  const contexts=[],requests=[];
  const meta={route:"SQL",semantic_scope:semanticScope,semantic_counts:semanticCounts,
    counts:{canonical_questions:1,occurrences:3},pending_counts:pendingCounts,
    pagination:{total:2,result_total:2,returned:2,offset:0,page_size:20,top_n:null,
      next_cursor:"PRIVATE_SIGNED_CURSOR"},private_path:"PRIVATE_SOURCE_PATH"};
  if(useHarness)meta.harness={evidence:rows,session:{current_page_ids:["confirmed","pending"],
    current_page_summary:rows,has_next_page:true}};
  const hostResult={answer:"One confirmed question; one pending-only question.",facts:{
    data:rows.map(row=>({...row,source_text:"PRIVATE_SOURCE_BODY".repeat(2000),
      pending_sources:[{raw_question_text:"PRIVATE_PENDING_BODY".repeat(2000),source_path:"PRIVATE_SOURCE_PATH"}]})),meta}};
  const server=http.createServer(async(req,res)=>{
    let body="";for await(const chunk of req)body+=chunk;
    requests.push(JSON.parse(body));
    res.writeHead(200,{"Content-Type":"application/json"});
    res.end(JSON.stringify(requests.length===1 ? hostResult : {
      answer:"Synthetic final answer",facts:{data:[],meta:{route:"ANSWER"}}}));
  });
  await new Promise(resolve=>server.listen(0,"127.0.0.1",resolve));
  t.after(()=>new Promise(resolve=>server.close(resolve)));
  const common={filters:Object.fromEntries(Object.entries(input_schema.$defs.FilterSpec.properties).map(([key,value])=>[key,value.default])),
    sort:"frequency",top_n:null,page_size:20};
  const fake=fauxProvider();fake.setResponses([
    fauxAssistantMessage(fauxToolCall("list_questions",{...common,action:"LIST",final:false})),
    fauxAssistantMessage(fauxToolCall("answer_question",{...common,action:"ANSWER",final:true,
      answer_text:"Synthetic final answer",answer_kind:"EXPLAIN",answer_basis:"CORPUS",question_ids:[]}))]);
  const models=createModels();models.setProvider(fake.provider);

  const result=await runQuery({run_id:"pending-evidence",schema:structuredClone(input_schema),
    prompt:"Explain confirmed and pending rows using their declared status.",context:{message:"Explain these results.",
      tool_policy:{allowed_actions:{list_questions:["LIST"],answer_question:["ANSWER"]}}}},
    {hostUrl:`http://127.0.0.1:${server.address().port}`,token:"synthetic-token",streamFn:(_model,context,options)=>{
      contexts.push(JSON.parse(JSON.stringify(context)));return models.streamSimple(fake.getModel(),context,options);
    }});

  assert.equal(result.completed,true);
  assert.equal(result.provider_calls,2);
  assert.equal(result.tool_calls,2);
  const message=contexts[1].messages.find(message=>message.role==="toolResult");
  assert.ok(message,"the actual second model call must receive the authoritative first result");
  const text=message.content.find(block=>block.type==="text").text;
  const evidence=JSON.parse(text);
  assert.deepEqual(evidence.items,rows);
  assert.deepEqual(evidence.meta.semantic_scope,semanticScope);
  assert.deepEqual(evidence.meta.semantic_counts,semanticCounts);
  assert.deepEqual(evidence.meta.pending_counts,pendingCounts);
  assert.deepEqual(evidence.counts,{canonical_questions:1,occurrences:3});
  assert.equal(evidence.meta.pagination.has_next_page,true);
  if(useHarness)assert.deepEqual(evidence.session.current_page_summary,rows);
  for(const marker of ["PRIVATE_SIGNED_CURSOR","PRIVATE_SOURCE_PATH","PRIVATE_SOURCE_BODY",
    "PRIVATE_PENDING_BODY","pending_sources"])
    assert.ok(!text.includes(marker),`${marker} must stay outside subsequent model evidence`);
});
