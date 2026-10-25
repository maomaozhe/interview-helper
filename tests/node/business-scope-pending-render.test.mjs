import test from "node:test";
import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";
import {createRequire} from "node:module";
import vm from "node:vm";

const core=createRequire(import.meta.url)("../../src/interview_intelligence/web/assets/core.js");

async function renderer(){
  const source=await readFile(new URL("../../src/interview_intelligence/web/assets/app.js",import.meta.url),"utf8");
  const body=source.slice(source.indexOf("function semanticReviewHTML("),source.indexOf("function usageFilters("))
    + source.slice(source.indexOf("function resultRow("),source.indexOf("let searchController"))
    + source.slice(source.indexOf("function renderAssistant("),source.indexOf("function startQueryProgress("))
    + source.slice(source.indexOf("function semanticLibraryRequest("),source.indexOf("async function search("));
  const context={state:{topicLabels:{}},labels:{},pretty:JSON.stringify,pickFilters:value=>value,
    canRequery:()=>true,answerIntroHTML:()=>"",window:{InterviewWorkspace:{
      feedbackOrigin:()=>({context:{run_id:"run",conversation_id:"conversation"}}),clarificationFor:()=>null}},
    escapeHTML:value=>String(value).replace(/[&<>\"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"})[c])};
  vm.createContext(context);vm.runInContext(body,context);return context;
}

test("business-scope pending shows its own badge and visible reason while keeping engineering facts distinct",async()=>{
  const context=await renderer();
  const row={canonical_question_id:"pending-application",canonical_text:"客服应用",occurrence_count:7,
    semantic_verification:"CONFIRMED",confirmed_occurrence_count:7,relevance_scope_status:"PENDING",
    relevance_scope_reason:"业务目标尚未核清 <script>bad()</script>"};
  const html=context.rowHTML(row,0);
  assert.match(html,/业务范围待核验/);assert.match(html,/已确认工程设计/);
  assert.match(html,/<p class="notice scope-verification-reason">业务目标尚未核清 &lt;script&gt;/);
  assert.doesNotMatch(html,/已确认业务范围|待核验工程设计归属|<script>/);
  assert.match(html,/<strong>7<\/strong><small>已确认提问/);
  assert.match(html,/question-number">01</);
  const confirmed=context.rowHTML({...row,relevance_scope_status:"CONFIRMED",relevance_scope_reason:""},1);
  assert.match(confirmed,/已确认业务范围/);assert.doesNotMatch(confirmed,/业务范围待核验|scope-verification-reason/);
});

test("legacy nested retrieval pending state remains visible and does not relabel source frequency",async()=>{
  const context=await renderer();
  const html=context.rowHTML({canonical_question_id:"application",canonical_text:"设计应用",occurrence_count:4,
    retrieval:{relevance_scope_status:"PENDING",relevance_scope_reason:"独立范围核验暂未完成"}},0);
  assert.match(html,/业务范围待核验/);assert.match(html,/独立范围核验暂未完成/);
  assert.match(html,/<strong>4<\/strong><small>真实提问/);
  assert.doesNotMatch(html,/已确认业务范围|已确认工程设计|待核验工程设计归属/);
});

test("candidate page caption reports confirmed and pending separately and retains next/requery actions",async()=>{
  const context=await renderer(),reply={dataset:{},addEventListener(){}};
  context.renderAssistant(reply,{intent:"SEARCH",facts:{data:[{canonical_question_id:"pending",canonical_text:"应用",
    relevance_scope_status:"PENDING",relevance_scope_reason:"业务目标待核验"}],meta:{
      relevance_status:"VERIFIED",returned_count:20,relevance_scope_hash:"hash",candidate_pool_limit:50,
      scope_candidate_counts:{confirmed:18,pending:3,visible:21},
      pagination:{kind:"semantic_candidates",next_cursor:"opaque"}}}},"下一页");
  assert.match(reply.innerHTML,/当前候选 21 道：业务范围已确认 18 道 · 待核验 3 道；候选池上限 50 道/);
  assert.match(reply.innerHTML,/业务范围待核验/);assert.doesNotMatch(reply.innerHTML,/已筛选相关题/);
  assert.match(reply.innerHTML,/chat-next/);assert.match(reply.innerHTML,/chat-requery/);
  assert.equal(context.scopeCandidateSummary({scope_candidate_counts:{confirmed:2,pending:-1,visible:1}}),"");
});

test("SEARCH candidate continuation survives restored history and its real next callback submits the same conversation",async()=>{
  const context=await renderer(),handlers=[],input={value:""},submitted=[];
  context.window.InterviewWorkspace=core;
  context.$=()=>input;
  context.sendChat=()=>submitted.push(input.value);
  const result={intent:"SEARCH",run_id:"candidate-run",conversation_id:"candidate-conversation",conversation_version:5,
    facts:{data:[],meta:{route:"SEARCH",pagination:{kind:"semantic_candidates",next_cursor:"signed-candidate-cursor"}}}};
  const history={conversation_id:result.conversation_id,version:5,state:{
    list_request:{kind:"semantic_candidates",cursor:"signed-candidate-cursor"}},
    turns:[{run_id:result.run_id,status:"SUCCEEDED",result}]};
  context.state.chatConversation={id:result.conversation_id,version:5};
  context.state.chatContinuation=core.historyContinuation(history);
  assert.equal(context.state.chatContinuation.next,"signed-candidate-cursor");
  const reply={dataset:{},addEventListener:(_name,callback)=>handlers.push(callback)};
  context.renderAssistant(reply,result,"按题意查找候选");
  assert.match(reply.innerHTML,/chat-next/);
  const button={dataset:{},classList:{contains:name=>name==="chat-next"}};
  const event={target:{closest:()=>button},stopPropagation(){}};
  handlers[0](event);
  assert.deepEqual(submitted,["下一页"]);
  context.state.chatConversation.version=6;
  handlers[0](event);
  assert.deepEqual(submitted,["下一页"],"stale result must not submit against a newer conversation");
  assert.equal(core.historyContinuation({...history,state:{list_request:{cursor:"new-cursor"}}}).next,null);
});

test("library semantic next and changed controls use the conversation route rather than a SQL list request",async()=>{
  const context=await renderer();
  context.state.libraryConversation={id:"candidate-conversation",version:7};
  context.crypto={randomUUID:()=>"request"};
  context.$=()=>({value:"importance"});
  const next=context.semanticLibraryRequest({isCandidatePage:true,cursor:"signed-candidate-cursor"});
  assert.equal(next.message,"下一页");assert.equal(next.conversation_id,"candidate-conversation");
  assert.equal(next.expected_version,7);assert.equal(next.list_request,undefined);
  const requery=context.semanticLibraryRequest({isCandidatePage:true,structured:true,
    query:"按题意查找候选",filters:{company:"测试公司"},pageSize:7,pipeline:"HYBRID_RERANK",origin:"candidate-run"});
  assert.equal(requery.message,"重新检索，按重要性排序");
  assert.equal(requery.requery_of_run_id,"candidate-run");assert.equal(requery.page_size,7);
  assert.equal(requery.filters.company,"测试公司");assert.equal(requery.list_request,undefined);
  assert.equal(context.semanticLibraryRequest({isCandidatePage:false,cursor:"sql-cursor"}),null);
  assert.equal(context.semanticLibraryRequest({isCandidatePage:true,query:""}),null);
  assert.throws(()=>context.semanticLibraryRequest({isCandidatePage:true,cursor:"semantic-cursor",routerEnabled:false}),
    /候选分页需要查询服务/);
  assert.throws(()=>context.semanticLibraryRequest({isCandidatePage:true,structured:true,query:"候选",routerEnabled:false}),
    /候选分页需要查询服务/);
  assert.equal(context.semanticLibraryRequest({isCandidatePage:false,cursor:"sql-cursor",routerEnabled:false}),null);
});

test("actual library restore and next keep a SEARCH cursor out of SQL when the router is enabled or disabled",async()=>{
  const source=await readFile(new URL("../../src/interview_intelligence/web/assets/app.js",import.meta.url),"utf8");
  for(const enabled of [true,false]){
    const context=await renderer(),nodes=new Map(),calls=[];
    const node=id=>{
      if(!nodes.has(id))nodes.set(id,{value:({query:"按题意查找候选","result-limit":"5","search-pipeline":"HYBRID_RERANK"})[id] || "",
        options:[],replaceChildren(){},querySelectorAll:()=>[]});
      return nodes.get(id);
    };
    Object.assign(context,{$:node,filters:()=>({}),healthSnapshot:{query_router_enabled:enabled},
      libraryInitialized:false,libraryProgress:null,searchController:null,AbortController,
      crypto:{randomUUID:()=>"request"},sessionStorage:{setItem(){}},
      notice(){},loading:()=>"loading",empty:()=>"empty",refreshWorkspaceForResult:async()=>{},
      api:async path=>{calls.push({kind:"api",path});throw new Error("unexpected SQL/API route");},
      listWithContext:async()=>{calls.push({kind:"SQL"});throw new Error("unexpected SQL route");},
      agentQuery:async body=>{calls.push({kind:"agent",body});return {data:[],warnings:[],elapsed_ms:1,meta:{
        route:"SEARCH",run_id:"next",conversation_id:"conversation",conversation_version:8,
        pagination:{kind:"semantic_candidates",next_cursor:null}}};}});
    context.window.InterviewWorkspace=core;
    context.state.searchSequence=0;
    vm.runInContext(source.slice(source.indexOf("async function search("),source.indexOf("async function showDetail(")),context);
    await context.search(null,false,null,null,{run_id:"first",conversation_id:"conversation",conversation_version:7,
      planning:{spec:{action:"SEARCH"}},facts:{data:[],meta:{route:"SEARCH",
        pagination:{kind:"semantic_candidates",next_cursor:"signed-semantic-cursor"}}}});
    assert.equal(context.state.libraryCandidatePage,true);
    assert.equal(context.state.nextCursor,"signed-semantic-cursor");
    await context.search(context.state.nextCursor);
    if(enabled){
      assert.equal(calls.length,1);assert.equal(calls[0].kind,"agent");
      assert.equal(calls[0].body.message,"下一页");assert.equal(calls[0].body.expected_version,7);
      assert.equal(context.state.libraryConversation.version,8);
      assert.equal(context.state.nextCursor,null);
    }else{
      assert.deepEqual(calls,[],"a semantic cursor must not be submitted to SQL or a legacy endpoint");
      assert.match(context.state.diagnostic.error,/候选分页需要查询服务/);
    }
  }
});
