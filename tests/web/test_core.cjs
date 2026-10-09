const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const corePath = path.resolve(__dirname, '../../src/interview_intelligence/web/assets/core.js');
const core = fs.existsSync(corePath) ? require(corePath) : {};

test('question detail keeps only the filters supported by its API', () => {
  assert.equal(typeof core.pickFilters, 'function');
  assert.deepEqual(core.pickFilters({company:'字节',topic_l1:'Redis',round:'SECOND',
    group_by:'question',topic_level:'L2',sort:'frequency',limit:20,cursor:null}),
    {company:'字节',topic_l1:'Redis',round:'SECOND'});
});

test('detail filters preserve the date range, language and question type', () => {
  assert.equal(typeof core.pickFilters, 'function');
  const filters = {start_date:'2026-07-01',end_date:'2026-10-02',date_basis:'BEST_AVAILABLE',
    language:'JAVA',question_type:'SCENARIO',topic_l2:'JVM'};
  assert.deepEqual(core.pickFilters(filters), filters);
  assert.deepEqual(core.pickFilters(null), {});
});

test('an older health response cannot undo an already synchronized import', () => {
  const ready = {current_revision:57,indexed_revision:57,index:'ready'};
  const earlier = {current_revision:57,indexed_revision:56,index:'stale'};
  assert.deepEqual(core.selectHealthSnapshot(ready, earlier), ready);
  assert.deepEqual(core.selectHealthSnapshot(earlier, ready), ready);
  assert.deepEqual(core.selectHealthSnapshot(ready,
    {current_revision:56,indexed_revision:56,index:'ready'}), ready);
});

test('new corpus progress and a service failure at the same revision remain visible', () => {
  const ready = {current_revision:57,indexed_revision:57,index:'ready'};
  const importing = {current_revision:58,indexed_revision:57,index:'stale'};
  const unavailable = {...ready,index:'unavailable'};
  assert.deepEqual(core.selectHealthSnapshot(null, ready), ready);
  assert.deepEqual(core.selectHealthSnapshot(ready, importing), importing);
  assert.deepEqual(core.selectHealthSnapshot(ready, unavailable), unavailable);
});

test('a query on a newer corpus refreshes the workspace opened before import progressed', () => {
  const opened = {current_revision:71,indexed_revision:71,index:'ready'};
  assert.equal(core.shouldRefreshWorkspace(opened,{corpus_revision:75}),true);
  assert.equal(core.shouldRefreshWorkspace(opened,{corpus_revision:71}),false);
  assert.equal(core.shouldRefreshWorkspace({current_revision:76,indexed_revision:76,index:'ready'},
    {corpus_revision:75}),false);
});

test('queries recheck a lagging index without using absent result versions as progress', () => {
  assert.equal(core.shouldRefreshWorkspace({current_revision:75,indexed_revision:74,index:'stale'},
    {corpus_revision:75}),true);
  assert.equal(core.shouldRefreshWorkspace(null,{corpus_revision:75}),true);
  assert.equal(core.shouldRefreshWorkspace({current_revision:75,indexed_revision:75,index:'ready'},{}),false);
});

test('published document filters keep existing versions while updates wait or fail', () => {
  const documents = [
    {path:'published.md',status:'QUEUED',active_status:'INCLUDED'},
    {path:'failed-update.md',status:'FAILED',active_status:'INCLUDED'},
    {path:'excluded.md',status:'QUEUED',active_status:'EXCLUDED'},
    {path:'new.md',status:'QUEUED',active_status:null}
  ];
  const paths = status => documents.filter(item => core.matchesDocumentStatus(item,status)).map(item => item.path);
  assert.deepEqual(paths('INCLUDED'),['published.md','failed-update.md']);
  assert.deepEqual(paths('EXCLUDED'),['excluded.md']);
  assert.deepEqual(paths('QUEUED'),['published.md','excluded.md','new.md']);
  assert.deepEqual(paths('FAILED'),['failed-update.md']);
});

test('not yet published includes new queued running and failed sources', () => {
  const documents = ['PENDING','QUEUED','RUNNING','FAILED','NEEDS_REVIEW'].map(status => ({status,active_status:null}));
  assert.equal(documents.filter(item => core.matchesDocumentStatus(item,'PENDING')).length,5);
  assert.equal(core.matchesDocumentStatus({status:'RUNNING',active_status:'INCLUDED'},'PENDING'),false);
  assert.equal(core.matchesDocumentStatus({status:'QUEUED',active_status:'EXCLUDED'},'PENDING'),false);
});

test('changed content stays findable after a reimport enters the queue', () => {
  const queued = {status:'QUEUED',active_status:'INCLUDED',content_changed:true};
  assert.equal(core.matchesDocumentStatus(queued,'CHANGED'),true);
  assert.equal(core.matchesDocumentStatus({...queued,content_changed:false},'CHANGED'),false);
  assert.equal(core.matchesDocumentStatus(queued,''),true);
});

test('feedback carries an exact run reference without truncating authoritative results', () => {
  const origin=core.feedbackOrigin('Agent设计题',{run_id:'run-1',conversation_id:'c1',answer:'真实回答',
    planning:{spec:{action:'SEARCH'}},facts:{data:Array.from({length:40},(_,i)=>({canonical_question_id:String(i)})),
      meta:{request_id:'request-1',corpus_revision:293,applied_filters:{company:'腾讯'}}}});
  assert.equal(origin.context.run_id,'run-1');
  assert.equal(origin.context.answer,'真实回答');
  assert.deepEqual(origin.context.filters,{company:'腾讯'});
  assert.equal(origin.context.results,undefined); // Server loads the entire PG receipt, no first-ten snapshot.
});

test('requery uses current conversation version and explicit feedback instead of replaying an old receipt', () => {
  const body=core.requeryBody('Agent设计题',{id:'c1',version:7},['f1']);
  assert.deepEqual(body,{message:'Agent设计题',conversation_id:'c1',expected_version:7,feedback_ids:['f1']});
  assert.equal(body.request_id,undefined); // Caller must allocate a new request_id.
});

test('requery binds the original run while keeping the original short message and current conversation', () => {
  const origin=core.feedbackOrigin('Agent 应用设计',{run_id:'original-run',conversation_id:'old-c',
    planning:{spec:{action:'SEARCH',relevance_query:'Agent 上下文和记忆设计',pipeline:'HYBRID_RERANK'}},
    facts:{meta:{request_id:'original-request',applied_filters:{company:'腾讯'}}}});
  assert.deepEqual(core.requeryBody(origin.query,{id:'current-c',version:12},['correction-1'],origin),
    {message:'Agent 应用设计',conversation_id:'current-c',expected_version:12,
      feedback_ids:['correction-1'],requery_of_run_id:'original-run'});
  const fresh=core.requeryBody(origin.query,null,[],origin);
  assert.equal(fresh.conversation_id,null);
  assert.equal(fresh.request_id,undefined);
  assert.equal(fresh.filters,undefined); // The backend restores the authoritative scope.
  assert.equal(fresh.pipeline,undefined);
  assert.equal(fresh.page_size,undefined);
});

function clarificationResult(run='clarify-run',version=1){
  return {run_id:run,conversation_id:'c1',conversation_version:version,intent:'CLARIFY',
    facts:{meta:{route:'CLARIFY',clarification:{question:'准备哪个方向？',options:['Agent 应用设计','业务系统设计']}}}};
}

test('only the active clarification can continue the current version of the conversation', () => {
  const result=clarificationResult(),origin=core.feedbackOrigin('场景设计题',result);
  const continuation=core.answerContinuation(result),conversation={id:'c1',version:1};
  assert.equal(core.canContinueAnswer(origin,conversation,continuation,'clarification'),true);
  assert.equal(core.canContinueAnswer(origin,conversation,continuation,'clarification',true),false);
  assert.equal(core.canContinueAnswer(origin,{id:'c1',version:2},continuation,'clarification'),false);
  assert.equal(core.canContinueAnswer(origin,{id:'c2',version:1},continuation,'clarification'),false);
  assert.equal(core.canContinueAnswer({...origin,context:{...origin.context,run_id:'old-run'}},conversation,
    continuation,'clarification'),false);
  assert.equal(core.canContinueAnswer(origin,conversation,null,'clarification'),false);
  assert.equal(core.canContinueAnswer(origin,conversation,continuation,'next'),false);
});

test('answered clarification and older next-page controls cannot target a new result', () => {
  const old=core.feedbackOrigin('场景设计题',clarificationResult());
  const page={run_id:'page-1',conversation_id:'c1',conversation_version:2,
    facts:{meta:{pagination:{next_cursor:'cursor-2'}}}};
  const continuation=core.answerContinuation(page),origin=core.feedbackOrigin('腾讯高频题',page);
  assert.equal(core.canContinueAnswer(old,{id:'c1',version:2},continuation,'clarification'),false);
  assert.equal(core.canContinueAnswer(origin,{id:'c1',version:2},continuation,'next'),true);
  const newer=core.answerContinuation({...page,run_id:'page-2',conversation_version:3,
    facts:{meta:{pagination:{next_cursor:'cursor-3'}}}});
  assert.equal(core.canContinueAnswer(origin,{id:'c1',version:3},newer,'next'),false);
});

test('history restoration activates only a latest successful clarification matching pending state', () => {
  const result=clarificationResult(),history={conversation_id:'c1',version:1,
    state:{pending_clarification:{question:'准备哪个方向？'}},
    turns:[{run_id:'clarify-run',status:'SUCCEEDED',result}]};
  const origin=core.feedbackOrigin('场景设计题',result),conversation={id:'c1',version:1};
  assert.equal(core.canContinueAnswer(origin,conversation,core.historyContinuation(history),'clarification'),true);
  assert.equal(core.canContinueAnswer(origin,conversation,
    core.historyContinuation({...history,state:{}}),'clarification'),false);
  assert.equal(core.historyContinuation({...history,turns:[...history.turns,
    {run_id:'failed-follow-up',status:'FAILED'}]}),null);
  assert.equal(core.historyContinuation({...history,turns:[...history.turns,
    {run_id:'running-follow-up',status:'RUNNING'}]}),null);
});

test('restored next-page control requires its exact current cursor', () => {
  const result={run_id:'page-1',conversation_id:'c1',conversation_version:1,
    facts:{meta:{pagination:{next_cursor:'cursor-2'}}}};
  const history={conversation_id:'c1',version:1,state:{list_request:{cursor:'cursor-2'}},
    turns:[{run_id:'page-1',status:'SUCCEEDED',result}]};
  const origin=core.feedbackOrigin('腾讯高频题',result),conversation={id:'c1',version:1};
  assert.equal(core.canContinueAnswer(origin,conversation,core.historyContinuation(history),'next'),true);
  assert.equal(core.canContinueAnswer(origin,conversation,core.historyContinuation({...history,
    state:{list_request:{cursor:'cursor-3'}}}),'next'),false);
  assert.equal(core.canContinueAnswer(origin,conversation,core.historyContinuation({...history,
    turns:[...history.turns,{run_id:'detail-run',status:'SUCCEEDED',result:{facts:{meta:{}}}}]}),'next'),false);
});

test('clarification choices survive a missing meta block and are deduplicated', () => {
  const result={answer:'想看哪个方向？',planning:{spec:{action:'CLARIFY',clarification:'想看哪个方向？',
    clarification_options:['Agent 应用设计',' 业务系统设计 ','Agent 应用设计','']}}};
  assert.deepEqual(core.clarificationFor(result),{question:'想看哪个方向？',options:['Agent 应用设计','业务系统设计']});
  assert.equal(core.clarificationFor({answer:'查到了',planning:{spec:{action:'SEARCH'}}}),null);
  assert.deepEqual(core.clarificationFor({facts:{meta:{clarification:{question:'输入题号',options:[]}}}}),
    {question:'输入题号',options:[]});
});

test('current receipt choices take precedence over older plan choices', () => {
  const result=clarificationResult();result.planning={spec:{action:'CLARIFY',clarification:'旧问题',clarification_options:['旧选项']}};
  assert.equal(core.clarificationFor(result).question,'准备哪个方向？');
  assert.deepEqual(core.clarificationFor(result).options,['Agent 应用设计','业务系统设计']);
});

test('user-facing progress names actual operations without leaking tool arguments', () => {
  assert.deepEqual(core.queryStage({type:'stage',stage:'reranking',count:50}),
    {key:'reranking',label:'正在核对题目相关性 · 50 条候选'});
  assert.deepEqual(core.queryStage({type:'stage',stage:'tool',action:'CLARIFY'}),
    {key:'CLARIFY',label:'正在准备可选方向'});
  assert.equal(core.queryStage({type:'stage',stage:'tool',action:'SEARCH'}),null);
  assert.equal(core.queryStage({type:'model_request',messages:['secret']}),null);
  assert.deepEqual(core.queryStage({type:'stage',stage:'tool',action:'COUNT'}),
    {key:'COUNT',label:'正在核对全量数量'});
  assert.deepEqual(core.queryStage({type:'stage',stage:'tool',action:'ANSWER'}),
    {key:'ANSWER',label:'正在整理回答'});
});

test('answers distinguish general knowledge from interview evidence and count scopes', () => {
  const general={intent:'ANSWER',facts:{meta:{answer_basis:'GENERAL_KNOWLEDGE',
    evidence_notice:'根据通用知识生成的参考回答。'}}};
  assert.equal(core.answerNotice(general),'通用知识参考 · 根据通用知识生成的参考回答。');
  const mixed={planning:{spec:{action:'ANSWER'}},facts:{meta:{answer_basis:'MIXED',
    evidence_notice:'题库来源仅证明题目与提问记录；解答为模型生成的参考内容。'}}};
  assert.match(core.answerNotice(mixed),/^题库记录 \+ 通用知识参考/);
  assert.match(core.answerNotice(mixed),/仅证明题目与提问记录/);
  assert.equal(core.answerNotice({intent:'COUNT',facts:{meta:{count_scope:'filtered_corpus'}}}),
    '按当前筛选范围统计全部记录');
  assert.equal(core.answerNotice({meta:{planning:{spec:{action:'COUNT'}},count_scope:'full_corpus'}}),
    '题库全部已发布记录的数量');
  assert.equal(core.answerNotice({intent:'SEARCH',facts:{meta:{answer_basis:'CORPUS'}}}),null);
});

test('chat and library answer introduction render Markdown and escape generated HTML', () => {
  const vm=require('node:vm');
  const source=fs.readFileSync(path.resolve(__dirname,'../../src/interview_intelligence/web/assets/app.js'),'utf8');
  const escape=source.slice(source.indexOf('const escapeHTML ='),source.indexOf('\nconst labels ='));
  const renderer=source.slice(source.indexOf('function answerIntroHTML('),source.indexOf('\nfunction renderAssistant('));
  const context=vm.createContext({window:{InterviewWorkspace:core,
    InterviewMarkdown:require('../../src/interview_intelligence/web/assets/markdown.js')}});
  vm.runInContext(escape+'\n'+renderer,context);
  const answer='## 解释第一步\n\n**代码**：\n\n```javascript\n  if (x < 3) return "ok";\n```\n\n<img src=x onerror=alert(1)>';
  const result={answer,intent:'ANSWER',facts:{meta:{answer_basis:'GENERAL_KNOWLEDGE',
    evidence_notice:'<script>不能执行</script>'}}};
  const html=context.answerIntroHTML(result);
  assert.match(html,/class="answer-text markdown-body"/);
  assert.match(html,/<h2>解释第一步<\/h2>/);
  assert.match(html,/<strong>代码<\/strong>/);
  assert.ok(html.includes('  if (x &lt; 3) return &quot;ok&quot;;'));
  assert.ok(html.includes('&lt;img src=x onerror=alert(1)&gt;'));
  assert.ok(html.includes('&lt;script&gt;不能执行&lt;/script&gt;'));
  assert.doesNotMatch(html,/<img|<script/);
  const css=fs.readFileSync(path.resolve(__dirname,'../../src/interview_intelligence/web/assets/markdown.css'),'utf8');
  assert.match(css,/\.markdown-body\s*\{[^}]*white-space:\s*normal/);
  assert.match(css,/\.markdown-body pre\s*\{[^}]*white-space:\s*pre/);
});

test('count replies render a quantity without treating count facts as question rows', () => {
  const vm=require('node:vm');
  const source=fs.readFileSync(path.resolve(__dirname,'../../src/interview_intelligence/web/assets/app.js'),'utf8');
  const escape=source.slice(source.indexOf('const escapeHTML ='),source.indexOf('\nconst labels ='));
  const renderer=source.slice(source.indexOf('function answerIntroHTML('),source.indexOf('\nfunction startQueryProgress('));
  const context=vm.createContext({window:{InterviewWorkspace:core,
    InterviewMarkdown:require('../../src/interview_intelligence/web/assets/markdown.js')},pickFilters:core.pickFilters,
    pretty:JSON.stringify,canRequery:()=>false,
    resultRow:()=>{throw Error('COUNT cannot render question rows');}});
  vm.runInContext(escape+'\n'+renderer,context);
  const reply={dataset:{},addEventListener(){}};
  context.renderAssistant(reply,{intent:'COUNT',answer:'共 2452 道题。',facts:{data:[{canonical_questions:2452}],
    meta:{count_scope:'full_corpus',counts:{canonical_questions:2452}}}},'一共有多少条数据？');
  assert.match(reply.innerHTML,/共 2452 道题。/);
  assert.match(reply.innerHTML,/题库全部已发布记录的数量/);
  assert.doesNotMatch(reply.innerHTML,/没有找到|question-row/);
});
