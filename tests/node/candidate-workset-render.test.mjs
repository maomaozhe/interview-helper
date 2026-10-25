import test from "node:test";
import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";
import vm from "node:vm";

async function renderer(){
  const source=await readFile(new URL("../../src/interview_intelligence/web/assets/app.js",import.meta.url),"utf8");
  const body=source.slice(source.indexOf("function semanticReviewHTML("),source.indexOf("function usageFilters("))
    +source.slice(source.indexOf("function renderAssistant("),source.indexOf("function startQueryProgress("));
  const context={state:{topicLabels:{}},labels:{},pickFilters:value=>value,pretty:JSON.stringify,canRequery:()=>true,
    answerIntroHTML:()=>"",resultRow:()=>"ROW",escapeHTML:value=>String(value).replace(/[&<>\"]/g,
      c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"})[c]),
    window:{InterviewWorkspace:{feedbackOrigin:()=>({context:{run_id:"run",conversation_id:"conversation"}}),
      clarificationFor:()=>null}}};
  vm.createContext(context);vm.runInContext(body,context);return context;
}

function meta(overrides={}){
  return {pagination:{kind:"semantic_candidates",workset:true,next_cursor:"signed"},candidate_pool_limit:50,
    candidate_workset_progress:{version:"candidate_workset_progress_v1",candidate_total:320,processed:20,
      unprocessed:300,confirmed:8,pending:5,excluded:7,visible:13,batch_size:20,last_batch_count:20,
      processing_complete:false,next_requires_processing:false,...overrides}};
}

test("workset caption never presents its320 candidates as confirmed business results or a50-item limit",async()=>{
  const c=await renderer(),caption=c.scopeCandidateSummary(meta());
  assert.match(caption,/工程候选范围 320 道/);
  assert.match(caption,/业务范围已确认 8 道 · 待核验 5 道 · 已排除 7 道/);
  assert.match(caption,/已处理 20 道/);
  assert.match(caption,/还有 300 道未处理/);
  assert.doesNotMatch(caption,/上限|业务范围已确认 320|范围核验已完成|已核验 20 道/);
  assert.equal(c.scopeWorksetProgress(meta({visible:320})),null);
});

test("empty negative batch retains a processing continuation instead of claiming an empty collection",async()=>{
  const c=await renderer(),reply={dataset:{},addEventListener(){}};
  c.renderAssistant(reply,{intent:"SEARCH",facts:{data:[],meta:meta({confirmed:0,pending:0,excluded:20,
    visible:0,next_requires_processing:true})}},"下一页");
  assert.match(reply.innerHTML,/本批没有新增可展示的题目，还有 300 道候选未核验/);
  assert.match(reply.innerHTML,/继续核验并翻页 →/);
  assert.match(reply.innerHTML,/chat-next/);
  assert.match(reply.innerHTML,/chat-requery/);
  assert.doesNotMatch(reply.innerHTML,/没有找到符合条件|已显示到.*最后|需要补充/);
  assert.equal(reply.dataset.nextRequiresProcessing,"true");
});

test("restored cached page and completed workset retain truthful counts and use ordinary next labels",async()=>{
  const c=await renderer(),cached=meta();
  assert.equal(c.scopeNextLabel(cached),"下一页 →");
  assert.equal(c.scopeWorksetEmptyHTML(cached).includes("300"),true);
  const complete=meta({processed:320,unprocessed:0,confirmed:150,pending:20,excluded:150,visible:170,
    processing_complete:true,next_requires_processing:false});
  assert.match(c.scopeCandidateSummary(complete),/业务范围已确认 150 道 · 待核验 20 道/);
  assert.match(c.scopeCandidateSummary(complete),/候选已处理完/);
  assert.equal(c.scopeWorksetEmptyHTML(complete),"");
  const reply={dataset:{},addEventListener(){}};
  c.renderAssistant(reply,{intent:"SEARCH",facts:{data:[{}],meta:cached}},"下一页");
  assert.match(reply.innerHTML,/下一页 →/);
  assert.doesNotMatch(reply.innerHTML,/继续核验并翻页/);
  assert.equal(reply.dataset.nextRequiresProcessing,"false");
});

test("workset frequency keeps its engineering occurrence denominator instead of relabeling all occurrences as business",async()=>{
  const c=await renderer(),row={canonical_question_id:"business",canonical_text:"应用",occurrence_count:4,
    confirmed_occurrence_count:3,pending_occurrence_count:1,semantic_verification:"CONFIRMED",
    relevance_scope_status:"CONFIRMED",frequency_basis:"engineering_qualified_occurrences"};
  const html=c.rowHTML(row,0);
  assert.match(html,/<strong>4<\/strong><small>工程范围提问/);
  assert.match(html,/已确认业务范围/);
  assert.match(html,/另有 1 次提问待核验/);
  const ordinary=c.rowHTML({...row,frequency_basis:undefined},0);
  assert.match(ordinary,/<strong>3<\/strong><small>已确认提问/);
});
