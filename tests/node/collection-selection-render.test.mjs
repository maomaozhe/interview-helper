import test from "node:test";
import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";
import {createRequire} from "node:module";
import vm from "node:vm";

const require=createRequire(import.meta.url);
const core=require("../../src/interview_intelligence/web/assets/core.js");
const source=await readFile(new URL("../../src/interview_intelligence/web/assets/app.js",import.meta.url),"utf8");

function selectionResult(stage="categories") {
  const categories=Array.from({length:8},(_,index)=>({message:`查看分类：分类${index}`,label:`分类${index}`,
    kind:"category",counts:{confirmed:10,pending:0,pending_only:0,visible:10}}));
  const choices=[...categories,
    {message:"查看分类：分类不明确",label:"分类不明确",kind:"uncategorized",
      counts:{confirmed:12,pending:0,pending_only:0,visible:12}},
    {message:"查看分类：待核验",label:"待核验",kind:"pending",
      counts:{confirmed:3,pending:22,pending_only:19,visible:22}},
    {message:"全量分页",label:"全量分页",kind:"all",
      counts:{confirmed:301,pending:22,pending_only:19,visible:320}}];
  return {run_id:"scope-preview",conversation_id:"conversation",conversation_version:1,intent:"CLARIFY",
    answer:"已确认 301 道，另有 19 道待核验题，共可浏览 320 道。",
    planning:{spec:{action:"CLARIFY",clarification_options:["只取模型的四项"]}},facts:{data:[],meta:{route:"CLARIFY",
      clarification:{question:"已确认 301 道，另有 19 道待核验题，共可浏览 320 道。",options:["全量分页","按分类浏览"]},
      collection_selection:{version:"collection_selection_v1",stage,
        counts:{confirmed:301,pending:22,pending_only:19,visible:320},choices,
        classification_notice:"按已有主题组织浏览；分类不明确和待核验题单独保留。"}}}};
}

function renderer() {
  const context={escapeHTML:value=>String(value).replace(/[&<>\"]/g,
    character=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"})[character])};
  vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf("function clarificationHTML("),source.indexOf("function canRequery(")),context);
  return context;
}

test("host collection menu keeps every category plus unclear, pending and full collection reachable",()=>{
  const result=selectionResult(),clarification=core.clarificationFor(result);
  assert.equal(clarification.options.length,11);
  assert.deepEqual(clarification.options,result.facts.meta.collection_selection.choices.map(choice=>choice.message));
  const html=renderer().clarificationHTML(clarification);
  assert.equal((html.match(/data-clarification=/g) || []).length,11);
  for(const label of ["分类0","分类7","分类不明确","待核验","全量分页"])
    assert.ok(html.includes(`data-clarification="${label==="全量分页" ? label : "查看分类："+label}"`));
  assert.match(html,/可浏览 320 道/);
  assert.match(html,/22 道含待核验来源/);
  assert.doesNotMatch(html,/可浏览 323 道/); // Confirmed and pending may overlap.
  assert.match(html,/分类不明确和待核验题单独保留/);
});

test("generic model clarification remains limited while only receipt facts can declare a collection menu",()=>{
  const result=selectionResult();
  result.planning.spec.clarification_options=Array.from({length:12},(_,index)=>`模型选项${index}`);
  result.planning.spec.collection_selection=result.facts.meta.collection_selection;
  delete result.facts.meta.collection_selection;
  delete result.facts.meta.clarification;
  const clarification=core.clarificationFor(result);
  assert.equal(clarification.options.length,4);
  assert.equal(clarification.collection_selection,undefined);
  result.facts.meta.collection_selection={version:"collection_selection_v2",stage:"categories",choices:[]};
  assert.equal(core.collectionSelectionFor(result),null);
  result.facts.meta.collection_selection={version:"collection_selection_v1",stage:"categories",
    choices:[{message:"unbound",label:"unexpected",kind:"invented"}]};
  assert.equal(core.collectionSelectionFor(result),null);
});

test("collection menu escapes labels, messages and notices without changing the submitted host message",()=>{
  const result=selectionResult();
  result.facts.meta.collection_selection.choices=[{message:'查看分类：<script>"x"</script>',
    label:'<img src=x onerror="alert(1)">',kind:"uncategorized",counts:{visible:2,pending:1}}];
  result.facts.meta.collection_selection.classification_notice="<script>不能执行</script>";
  const clarification=core.clarificationFor(result),html=renderer().clarificationHTML(clarification,false);
  assert.equal(clarification.options[0],'查看分类：<script>"x"</script>');
  assert.match(html,/data-clarification="查看分类：&lt;script&gt;&quot;x&quot;&lt;\/script&gt;"/);
  assert.match(html,/&lt;img src=x onerror=&quot;alert\(1\)&quot;&gt;/);
  assert.match(html,/&lt;script&gt;不能执行&lt;\/script&gt;/);
  assert.doesNotMatch(html,/<script|<img/);
  assert.doesNotMatch(html,/<p>已确认 301/);
});

test("large host category catalogues are scrollable and keep the last pending and full entry",()=>{
  const result=selectionResult();
  const choices=result.facts.meta.collection_selection.choices;
  choices.unshift(...Array.from({length:150},(_,index)=>({message:`查看分类：扩展分类${index}`,
    label:`扩展分类${index}`,kind:"category",counts:{visible:1}})));
  const clarification=core.clarificationFor(result),html=renderer().clarificationHTML(clarification);
  assert.equal(clarification.options.length,161);
  assert.equal((html.match(/data-clarification=/g) || []).length,161);
  assert.match(html,/data-clarification="查看分类：待核验"/);
  assert.match(html,/data-clarification="全量分页"/);
  assert.match(html,/clarification-choices collection-choices/);
});

test("selected collection receipts do not reopen a clarification",()=>{
  const result=selectionResult("selected");
  result.intent="LIST";
  result.planning.spec={action:"LIST"};
  delete result.facts.meta.clarification;
  result.facts.meta.route="SQL";
  result.facts.meta.collection_selection.choices=[];
  result.facts.meta.collection_selection.selected={kind:"all"};
  assert.equal(core.clarificationFor(result),null);
});

test("collection controls continue only their active host conversation and survive safe history restore",()=>{
  const result=selectionResult(),origin=core.feedbackOrigin("列出所有工程系统设计题",result);
  const history={conversation_id:"conversation",version:1,
    state:{pending_clarification:{question:result.facts.meta.clarification.question}},
    turns:[{run_id:result.run_id,status:"SUCCEEDED",result}]};
  const continuation=core.historyContinuation(history);
  assert.equal(core.canContinueAnswer(origin,{id:"conversation",version:1},continuation,"clarification"),true);
  assert.equal(core.canContinueAnswer(origin,{id:"conversation",version:2},continuation,"clarification"),false);
  assert.equal(core.canContinueAnswer(origin,{id:"other",version:1},continuation,"clarification"),false);
  assert.equal(core.historyContinuation({...history,turns:[...history.turns,{status:"RUNNING"}]}),null);
});

test("library page restores the typed host category scope without reconstructing it from a model plan",()=>{
  const request={company:"字节",round:"SECOND",semantic_task:"ENGINEERING_DESIGN",collection_category:"pending",
    sort:"gap",page_size:7,top_n:120,cursor:"old-cursor",review_statuses:["WEAK"],review_order:"AFTER_TOP_N"};
  const result={planning:{spec:{action:"LIST",filters:{company:"模型推断公司"},sort:"frequency",page_size:20}},
    facts:{meta:{list_request:request}}};
  const restored=core.listRequestFor(result);
  assert.deepEqual(restored,{...request,cursor:null});
  assert.equal(request.cursor,"old-cursor");
  const next={...restored,cursor:"signed-next-cursor"};
  assert.equal(next.collection_category,"pending");
  assert.equal(next.semantic_task,"ENGINEERING_DESIGN");
  const restart={...restored,sort:"importance",page_size:13,cursor:null};
  assert.equal(restart.collection_category,"pending");
  assert.equal(restart.company,"字节");
  assert.equal(core.listRequestFor({...result,planning:{spec:{action:"CLARIFY"}}}),null);
});

test("older list receipts still retain their audited semantic task when the library requests the next page",()=>{
  const result={meta:{planning:{spec:{action:"LIST",filters:{company:"腾讯"},semantic_task:"ENGINEERING_DESIGN",
    sort:"frequency",top_n:null,page_size:17,review_statuses:[],review_order:"BEFORE_TOP_N"}}}};
  assert.deepEqual(core.listRequestFor(result),{company:"腾讯",semantic_task:"ENGINEERING_DESIGN",sort:"frequency",
    top_n:null,page_size:17,review_statuses:[],review_order:"BEFORE_TOP_N"});
  assert.equal(core.listRequestFor({meta:{planning:{spec:{action:"SEARCH"}}}}),null);
});

test("synchronizing active controls updates the footer instead of overwriting category counts",()=>{
  const hint={textContent:"footer"},count={textContent:"可浏览 320 道"},selectors=[];
  const reply={dataset:{runId:"run",conversationId:"conversation"},querySelectorAll:()=>[],
    querySelector(selector){selectors.push(selector);return selector===".clarification .clarification-hint" ? hint :
      selector===".clarification small" ? count : null;}};
  const context={state:{chatConversation:{id:"conversation",version:1},chatContinuation:{},chatBusy:false},
    window:{InterviewWorkspace:{canContinueAnswer:()=>true}},$:()=>({querySelectorAll:()=>[reply]})};
  vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf("function syncContinuationActions("),source.indexOf("function answerIntroHTML(")),context);
  context.syncContinuationActions();
  assert.equal(count.textContent,"可浏览 320 道");
  assert.match(hint.textContent,/也可以直接补充/);
  assert.ok(selectors.includes(".clarification .clarification-hint"));
});
