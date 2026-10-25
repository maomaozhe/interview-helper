import test from "node:test";
import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";
import http from "node:http";
import {createModels,fauxProvider,fauxAssistantMessage,fauxToolCall} from "@earendil-works/pi-ai";
import {runQuery} from "../../services/pi-agent/runtime.mjs";

const {input_schema}=JSON.parse(await readFile(new URL("../fixtures/query-tool-contract-before.json",import.meta.url),"utf8"));
const {cases}=JSON.parse(await readFile(new URL("../fixtures/answer-coverage-evidence.json",import.meta.url),"utf8"));
const common={filters:Object.fromEntries(Object.entries(input_schema.$defs.FilterSpec.properties)
  .map(([key,value])=>[key,value.default])),sort:"frequency",top_n:null,page_size:20};

async function nextModelEvidence(t,meta,useHarness) {
  const contexts=[],requests=[];
  const hostMeta={...structuredClone(meta),private_metadata:{source:"PRIVATE_FULL_SOURCE".repeat(2000)},
    candidate_ids:["PRIVATE_CANDIDATE"],rerank_audit:{candidates:["PRIVATE_AUDIT"]},next_cursor:"PRIVATE_SIGNED_CURSOR"};
  if(hostMeta.result_completeness && typeof hostMeta.result_completeness==="object")
    Object.assign(hostMeta.result_completeness,{private_metadata:{source:"PRIVATE_NESTED_SOURCE".repeat(2000)},
      next_cursor:"PRIVATE_NESTED_CURSOR"});
  if(useHarness)hostMeta.harness={evidence:[],session:{current_page_ids:[]}};
  const server=http.createServer(async(req,res)=>{
    let body="";for await(const chunk of req)body+=chunk;
    requests.push(JSON.parse(body));
    res.writeHead(200,{"Content-Type":"application/json"});
    res.end(JSON.stringify(requests.length===1 ? {answer:"Bounded search receipt",facts:{data:[],meta:hostMeta}} :
      {answer:"The available evidence has a declared coverage boundary.",facts:{data:[],meta:{route:"ANSWER"}}}));
  });
  await new Promise(resolve=>server.listen(0,"127.0.0.1",resolve));
  t.after(()=>new Promise(resolve=>server.close(resolve)));
  const provider=fauxProvider();provider.setResponses([
    fauxAssistantMessage(fauxToolCall("search_questions",{...common,action:"SEARCH",search_query:"synthetic scope",
      pipeline:"HYBRID_RERANK",final:false})),
    fauxAssistantMessage(fauxToolCall("answer_question",{...common,action:"ANSWER",final:true,
      answer_text:"The available evidence has a declared coverage boundary.",answer_kind:"EXPLAIN",
      answer_basis:"CORPUS",question_ids:[]}))]);
  const models=createModels();models.setProvider(provider.provider);
  const result=await runQuery({run_id:"answer-coverage",schema:structuredClone(input_schema),prompt:"Assess scope after reading the tools.",
    context:{message:"Are these all the results? Explain your evidence.",tool_policy:{
      allowed_actions:{search_questions:["SEARCH"],answer_question:["ANSWER"]}}}},
    {hostUrl:`http://127.0.0.1:${server.address().port}`,token:"synthetic-token",streamFn:(_model,context,options)=>{
      contexts.push(structuredClone(context));return models.streamSimple(provider.getModel(),context,options);
    }});
  assert.equal(result.completed,true);assert.equal(result.provider_calls,2);assert.equal(result.tool_calls,2);
  assert.deepEqual(requests.map(plan=>plan.action),["SEARCH","ANSWER"]);
  const tool=contexts[1].messages.find(message=>message.role==="toolResult");
  assert.ok(tool,"actual follow-up model invocation must read the executed tool receipt");
  const wire=tool.content.find(block=>block.type==="text").text;
  assert.ok(!wire.includes("PRIVATE"),"source bodies, extra nested metadata, audits and cursors stay outside model context");
  return JSON.parse(wire);
}

for(const useHarness of [true,false])for(const scenario of cases)
  test(`next Pi answer receives bounded coverage: ${scenario.id} (${useHarness ? "host harness" : "legacy fallback"})`,async t=>{
    const evidence=await nextModelEvidence(t,scenario.meta,useHarness);
    assert.deepEqual(evidence.meta,scenario.meta);
    assert.deepEqual(evidence.items,[]);
  });

for(const useHarness of [true,false])
  test(`next Pi answer rejects malformed coverage primitives (${useHarness ? "host harness" : "legacy fallback"})`,async t=>{
    const evidence=await nextModelEvidence(t,{route:"SEARCH",candidate_count:true,eligible_count:-1,
      returned_count:"0",invalid_candidate_count:Number.MAX_SAFE_INTEGER+1,invalid_evidence_count:1.5,
      invalid_schema_count:null,candidate_verification_status:{raw:"PRIVATE_STATUS"},verification_basis:"PRIVATE_BASIS",
      result_completeness:{candidate_count:50,eligible_count:{raw:"PRIVATE_COUNT"},returned_count:0,
        corpus_complete:false,verification_complete:1,scope:["retrieval_candidates"],reason:"PRIVATE_REASON"}},useHarness);
    assert.deepEqual(evidence.meta,{route:"SEARCH",result_completeness:{
      candidate_count:50,returned_count:0,corpus_complete:false}});
  });
