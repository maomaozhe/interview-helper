"use strict";

const $ = (id) => document.getElementById(id);
const pickFilters = window.InterviewWorkspace.pickFilters;
const escapeHTML = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[char]));
const labels = {UNSEEN:"未复习", WEAK:"薄弱", REVIEWED:"已复习", MASTERED:"已掌握", KNOWLEDGE:"知识", PRINCIPLE:"原理", SCENARIO:"场景", SYSTEM_DESIGN:"系统设计", PROJECT:"项目", ALGORITHM:"算法", AI:"AI", HR:"HR", OTHER:"其他", INCLUDED:"已生效", PENDING:"尚未生效", EXCLUDED:"已排除", NEEDS_REVIEW:"待核验", QUEUED:"排队中", RUNNING:"导入中", SUCCEEDED:"已完成", FAILED:"失败", PARTIAL_FAILURE:"部分失败", IRRELEVANT:"检索偏题", TAG:"标签问题", DEDUP:"归并问题", SOURCE:"来源问题", HYBRID:"混合检索", BM25:"关键词检索", DENSE:"语义检索", HYBRID_RERANK:"混合检索 + 重排", CORE:"核心", COMMON:"常见", LONG_TAIL:"长尾"};
const state = {page:"library", taxonomy:{}, topicLabels:{}, documents:[], selected:new Set(), feedback:[], detail:null, diagnostic:null, feedbackContext:null, searchSequence:0, detailSequence:0, sourceSequence:0, rows:[], chatBusy:false, ingestBusy:false, libraryConversation:null, chatConversation:null, listRequest:null, nextCursor:null};
labels.CHANGED = "内容已修改";
labels.EXTRACT = "抽取问题"; labels.DEDUP = "核对与归并问题"; labels.PUBLISH = "发布数据"; labels.INDEX_SYNC = "同步检索索引";
Object.assign(labels,{SQL:"数据库查询",CODE:"代码实现",ENGINEERING:"工程实现",MIXED:"混合任务"});
Object.assign(labels,{VERBAL:"口头回答",EXPLANATION:"解释原理",DESIGN:"设计方案",NONE:"非编码任务",UNKNOWN:"未知",VERIFIED:"已人工核验"});
let toastTimer;
let healthSnapshot = null;

function toast(message) { $("toast").textContent = message; $("toast").hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(() => $("toast").hidden = true, 4000); }
function notice(id, message="", type="") { const node = $(id); node.textContent = message; node.className = `notice ${type}`; node.hidden = !message; }
function empty(title, text="") { return `<div class="empty"><span class="empty-mark">◇</span><strong>${escapeHTML(title)}</strong>${escapeHTML(text)}</div>`; }
function loading(text="正在读取…") { return `<div class="loading">${escapeHTML(text)}</div>`; }
function pretty(value) { return JSON.stringify(value, null, 2); }
function timestamp(value) { if (!value) return "—"; const date = new Date(/[Z+]|\d-\d\d:\d\d$/.test(value.slice(10)) ? value : `${value}Z`); return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", {hour12:false}); }
function safeLink(value) { try { const url = new URL(value); return ["http:","https:"].includes(url.protocol) ? url.href : ""; } catch { return ""; } }

function renderHealth(incoming) {
  const h = window.InterviewWorkspace.selectHealthSnapshot(healthSnapshot, incoming);
  healthSnapshot = h;
  $("connection-dot").className = h.database === "ready" ? "ready" : "error";
  $("connection-text").textContent = h.database !== "ready" ? "连接暂时不可用" : h.index === "ready" ? "服务已连接 · 索引就绪" : "服务已连接 · 索引同步中";
  $("footer-revision").textContent = `语料 v${h.current_revision} · 索引 v${h.indexed_revision}`;
  $("ingest-health").innerHTML = `<h3>服务与数据状态</h3><div class="health-item">数据库<b>${h.database==="ready" ? "已连接" : "不可用"}</b></div><div class="health-item">检索索引<b>${h.index==="ready" ? "已就绪" : "等待同步"}</b></div><div class="health-item">模型接口<b>${h.model==="configured" ? "已配置" : "尚未配置"}</b></div><div class="health-item">数据版本<b>语料 v${h.current_revision} / 索引 v${h.indexed_revision}</b></div>`;
}

async function api(path, {method="GET", params={}, body, signal}={}) {
  const url = new URL(path.replace(/^\/+/, ""), document.baseURI);
  for (const [key,value] of Object.entries(params)) if (value !== null && value !== undefined && value !== "") url.searchParams.set(key, value);
  const started = performance.now();
  let response;
  try { response = await fetch(url, {method, headers:body ? {"Content-Type":"application/json"} : {}, body:body ? JSON.stringify(body) : undefined, signal}); }
  catch(error) { if (error.name === "AbortError") throw error; throw new Error("无法连接本机服务，请检查服务状态后重试。"); }
  let payload;
  try { payload = await response.json(); } catch { throw new Error("服务返回了无法读取的响应，请刷新后重试。"); }
  if (!response.ok) {
    const messages = {MODEL_PROVIDER_TIMEOUT:"模型返回较慢，本次请求超时，请稍后重试。", MODEL_PROVIDER_UNAVAILABLE:"模型接口暂时不可用，请稍后重试。", STREAM_INCOMPLETE:"模型响应中断，未使用不完整的结果，请重试。", MODEL_OUTPUT_TRUNCATED:"模型响应未完整返回，请重试。", INDEX_NOT_READY:"题库与索引正在同步，请稍后重试。统计浏览仍可使用。", SOURCE_SNAPSHOT_MISSING:"这份来源快照暂时不可用。", SNAPSHOT_CHANGED:"语料正在更新，请重新查询。", MODEL_CONFIGURATION_INCOMPLETE:"模型尚未配置完整，暂时不能开始导入。", VALIDATION_ERROR:"提交内容不符合要求，请检查输入和筛选条件。"};
    const code = payload.error?.code;
    const error = new Error(`${messages[code] || payload.error?.message || "操作失败"}${payload.request_id ? `（请求编号 ${payload.request_id}）` : ""}`);
    error.payload = payload; throw error;
  }
  payload.elapsed_ms = Math.round(performance.now() - started); return payload;
}

function filters() { return pickFilters({topic_l1:$("filter-topic").value, topic_l2:$("filter-subtopic").value, company:$("filter-company").value, round:$("filter-round").value, question_type:$("filter-type").value,coding_focus:$("filter-coding").value,response_form:$("filter-response").value,annotation_status:$("filter-annotation").value}); }
async function listWithContext(request, signal, cursor) {
  const body={list_request:request,request_id:crypto.randomUUID(),conversation_id:state.libraryConversation?.id || null,
    expected_version:cursor ? state.libraryConversation?.version ?? null : null};
  // A changed explicit SQL scope replaces the previous scope. Use the latest
  // host version so an aborted filter request cannot leave the client stale.
  for(let attempt=0;attempt<3;attempt++) {
    try{return await api("api/questions/list",{method:"POST",body,signal});}
    catch(error){
      if(error.payload?.error?.code!=="QUERY_IN_PROGRESS" || attempt===2 || signal.aborted) throw error;
      await new Promise(resolve=>setTimeout(resolve,100));
    }
  }
}

async function agentQuery(body,signal,kind,onStage=()=>{}) {
  const started=performance.now();
  const key=kind==="library" ? "libraryConversation" : "chatConversation";
  if(!body.conversation_id) {
    const created=await api("api/conversations",{method:"POST",signal});
    body.conversation_id=created.data.conversation_id;
    body.expected_version=created.data.version;
    state[key]={id:body.conversation_id,version:body.expected_version};
  }
  sessionStorage.setItem(key,JSON.stringify(state[key]));
  sessionStorage.setItem(`${kind}Pending`,JSON.stringify(body));
  $(kind==="library" ? "query-retry" : "chat-retry").hidden=false;
  let accepted;
  try{accepted=await api(`api/conversations/${encodeURIComponent(body.conversation_id)}/messages`,{method:"POST",body,signal});}
  catch(error){
    if(error.name==="AbortError"){
      // Acceptance may have committed before the browser received its response.
      try{const receipt=await api(`api/query/receipts/${encodeURIComponent(body.request_id)}`);
        await api(`api/runs/${encodeURIComponent(receipt.data.run_id)}/cancel`,{method:"POST"});}catch{}
    }
    throw error;
  }
  const runId=accepted.data.run_id;
  const cancel=()=>api(`api/runs/${encodeURIComponent(runId)}/cancel`,{method:"POST"}).catch(()=>{});
  const snapshot=(await api(`api/runs/${encodeURIComponent(runId)}`)).data;
  if(signal?.aborted){cancel();throw new DOMException("查询已取消","AbortError");}
  const result=snapshot.status==="SUCCEEDED" ? snapshot.result : await new Promise((resolve,reject)=>{
    const url=new URL(`api/runs/${encodeURIComponent(runId)}/events`,document.baseURI);
    url.searchParams.set("after",snapshot.event_cursor || 0);
    const events=new EventSource(url);
    let ended=false, fallbackStarted=false;
    const clean=()=>{ended=true;events.close();clearTimeout(timer);signal?.removeEventListener("abort",abort);};
    const abort=()=>{clean();cancel();reject(new DOMException("查询已取消","AbortError"));};
    const timer=setTimeout(()=>{clean();cancel();reject(new Error("查询超时，请重试。"));},90000);
    signal?.addEventListener("abort",abort,{once:true});
    if(signal?.aborted) return abort();
    events.addEventListener("stage",e=>{if(!ended)onStage(JSON.parse(e.data));});
    events.addEventListener("tool_result",()=>{if(!ended)onStage({stage:"tool_completed"});});
    events.addEventListener("completed",e=>{clean();resolve(JSON.parse(e.data).result);});
    for(const event of ["failed","interrupted"]) events.addEventListener(event,async e=>{
      clean();const payload=JSON.parse(e.data),error=new Error(payload.code || "查询中断，请使用原请求重试。");
      try{error.partial=(await api(`api/runs/${encodeURIComponent(runId)}`)).data.partial_result;}catch{}
      reject(error);
    });
    events.onerror=async()=>{
      if(ended || fallbackStarted)return;
      fallbackStarted=true;
      events.close();
      // A disconnected event stream must not create another model run.
      try {
        while(!ended) {
          const response=await api(`api/runs/${encodeURIComponent(runId)}`,{signal}), run=response.data;
          if(run.status==="SUCCEEDED"){clean();resolve(run.result);return;}
          if(run.status!=="RUNNING"){clean();const error=new Error(run.error_code || "查询未完成");error.partial=run.partial_result;reject(error);return;}
          await new Promise(resolve=>setTimeout(resolve,250));
        }
      } catch(error){if(!ended){clean();reject(error);}}
    };
  });
  state[key]={id:result.conversation_id,version:result.conversation_version};
  sessionStorage.setItem(key,JSON.stringify(state[key]));
  sessionStorage.removeItem(`${kind}Pending`);
  $(kind==="library" ? "query-retry" : "chat-retry").hidden=true;
  const elapsed_ms=Math.round(performance.now()-started);
  if(kind==="chat")return {data:result,meta:result.facts.meta,elapsed_ms};
  return {data:result.facts.data || [],meta:{...result.facts.meta,answer:result.answer,
    conversation_id:result.conversation_id,conversation_version:result.conversation_version,
    planning:result.planning,tool_trace:result.tool_trace},warnings:result.warnings || [],elapsed_ms};
}

const preferenceOptions={
  coding_focus:[["ENGINEERING","工程代码实现"],["ALGORITHM","算法题"],["MIXED","混合任务"]],
  language:[["JAVA","Java"],["PYTHON","Python"],["CPP","C++"],["GO","Go"],["JAVASCRIPT","JavaScript"],["TYPESCRIPT","TypeScript"]],
  job_family:[["BACKEND","后端"],["AI_APPLICATION","AI 应用"],["ALGORITHM","算法"],["OTHER","其他"]],
  pipeline:[["HYBRID","混合检索"],["BM25","关键词检索"],["DENSE","语义检索"],["HYBRID_RERANK","混合检索 + 重排"]],
  page_size:[[20,"20 条"],[40,"40 条"],[50,"50 条"],[100,"100 条"]]
};
let savedPreferences=[];
function preferenceChoices(){
  const key=$("preference-key").value;
  $("preference-value").innerHTML=preferenceOptions[key].map(([value,label])=>`<option value="${value}">${label}</option>`).join("");
  const saved=savedPreferences.find(p=>p.key===key);
  if(saved)$("preference-value").value=String(saved.value);
}
async function loadPreferences(){
  try {
    const response=await api("api/preferences");savedPreferences=response.data;
    $("preferences-list").innerHTML=savedPreferences.length ? savedPreferences.map(p=>{
      const label=preferenceOptions[p.key]?.find(([value])=>String(value)===String(p.value))?.[1] || p.value;
      return `<article class="question-row"><div><strong>${escapeHTML(label)}</strong><p class="muted">${escapeHTML(p.source_message)}${p.active ? "" : " · 已过期"}</p></div><button type="button" class="text-button" data-delete-preference="${escapeHTML(p.key)}" data-version="${p.version}">删除</button></article>`;
    }).join("") : empty("尚未保存长期偏好","选择默认值后，点击保存。");
    preferenceChoices();
  } catch(error){notice("preferences-notice",error.message,"error");}
}
function topicOptions() {
  $("filter-topic").innerHTML = '<option value="">全部主题</option>' + Object.keys(state.taxonomy).map(name => `<option value="${escapeHTML(name)}">${escapeHTML(name)}</option>`).join("");
  subtopicOptions();
}
function subtopicOptions() { $("filter-subtopic").innerHTML = '<option value="">全部细分主题</option>' + Object.keys(state.taxonomy[$("filter-topic").value] || {}).map(name => `<option value="${escapeHTML(name)}">${escapeHTML(name)}</option>`).join(""); }

async function refreshWorkspace() {
  try {
    const [overview,health] = await Promise.all([api("api/workspace"),api("api/health")]);
    const data = overview.data, count = data.counts;
    $("count-documents").textContent = count.included_documents.toLocaleString();
    $("count-documents-caption").textContent = `本地 ${count.local_documents} 篇 · ${count.pending_documents} 篇待导入或更新`;
    $("count-questions").textContent = count.canonical_questions.toLocaleString();
    $("count-occurrences").textContent = count.occurrences.toLocaleString();
    $("count-occurrences-caption").textContent = `来自 ${count.interviews} 场真实面试`;
    const company = $("filter-company").value;
    $("filter-company").innerHTML = '<option value="">全部公司</option>' + data.companies.map(name => `<option value="${escapeHTML(name)}">${escapeHTML(name)}</option>`).join("");
    $("filter-company").value = company;
    renderHealth(health.data);
    notice("global-message");
  } catch(error) { $("connection-dot").className = "error"; $("connection-text").textContent = "连接暂时不可用"; notice("global-message",error.message,"error"); }
}

async function refreshWorkspaceForResult(meta) {
  if (window.InterviewWorkspace.shouldRefreshWorkspace(healthSnapshot, meta))
    await refreshWorkspace();
}

function rowHTML(row,index) {
  const id = escapeHTML(row.canonical_question_id || "");
  const topic = state.topicLabels[row.topic_id];
  const taskLabel = row.coding_focus && row.coding_focus !== "NONE" ? labels[row.coding_focus] || row.coding_focus : labels[row.question_type] || row.question_type;
  const meta = [row.importance_band ? `<span class="pill ${row.importance_band === "CORE" ? "green" : ""}">${escapeHTML(labels[row.importance_band])}</span>` : "", topic ? `<span class="pill">${escapeHTML(topic)}</span>` : "", taskLabel ? `<span class="pill">${escapeHTML(taskLabel)}</span>` : "", row.response_form ? `<span class="pill">${escapeHTML(row.response_form === "SQL" ? "SQL 实现" : labels[row.response_form] || row.response_form)}</span>` : "", row.user_status ? `<span class="pill ${row.user_status === "WEAK" ? "orange" : ""}">${escapeHTML(labels[row.user_status])}</span>` : "", row.interview_count != null ? `${row.interview_count} 场面试` : "", row.variants?.length ? `${row.variants.length} 种原始问法` : ""].filter(Boolean).join("");
  return `<article class="question-row"><span class="question-number">${String(index+1).padStart(2,"0")}</span><div class="question-main"><button class="question-title" data-detail="${id}">${escapeHTML(row.canonical_text || row.key || "题目")}</button><div class="question-meta">${meta || '<span>点开详情，查看标签与原文</span>'}</div></div><div class="frequency"><strong>${escapeHTML(row.occurrence_count ?? "—")}</strong><small>真实提问</small></div><div class="row-actions"><button class="button secondary small" data-detail="${id}">查看详情 ↗</button><button class="icon-button" data-issue="${id}" aria-label="记录这道题的问题" title="记录问题">⚑</button></div></article>`;
}
function resultRow(row,index){
  if(row.canonical_question_id)return rowHTML(row,index);
  return `<article class="question-row"><span class="question-number">${index+1}</span><div class="question-main"><strong>${escapeHTML(state.topicLabels[row.key] || row.key)}</strong><p class="muted">统计分组</p></div><div class="frequency"><strong>${row.occurrence_count ?? 0}</strong><small>真实提问</small></div></article>`;
}

let searchController,chatController;
let annotationQueue=null;
async function loadAnnotations(cursor=null){
  $("annotations-list").innerHTML=loading("正在读取待核验分类…");
  try{
    const response=await api("api/task-annotations",{params:{cursor,limit:20}});annotationQueue=response.data;
    const choices=(values,current)=>values.map(v=>`<option value="${v}" ${v===current ? "selected" : ""}>${escapeHTML(labels[v] || v)}</option>`).join("");
    $("annotations-list").innerHTML=annotationQueue.items.length ? annotationQueue.items.map(item=>`<article class="card"><h3>${escapeHTML(item.raw_question)}</h3><p class="muted">${escapeHTML(item.source_path)} · ${escapeHTML(labels[item.classification_status] || item.classification_status)}</p><button class="text-button" data-annotation-source="${escapeHTML(item.occurrence_id)}">查看引用原文 ↗</button><form data-annotation-id="${escapeHTML(item.occurrence_id)}"><label>作答方式<select name="response_form">${choices(["CODE","SQL","VERBAL","EXPLANATION","DESIGN","OTHER","UNKNOWN"],item.draft?.response_form || item.response_form)}</select></label><label>任务焦点<select name="coding_focus">${choices(["ENGINEERING","ALGORITHM","MIXED","NONE","UNKNOWN"],item.draft?.coding_focus || item.coding_focus)}</select></label><label>判断依据<textarea name="reason" rows="2" required maxlength="1000">${escapeHTML(item.draft?.reason || "")}</textarea></label><button class="button secondary" type="submit">保存核验草稿</button><label><input type="checkbox" data-publish-id="${escapeHTML(item.occurrence_id)}" ${item.draft ? "" : "disabled"}>发布此草稿</label></form></article>`).join("") : empty("当前没有待核验分类");
    $("annotations-next").hidden=!annotationQueue.next_cursor;$("annotations-publish").disabled=true;
  }catch(error){$("annotations-list").innerHTML=empty("待核验分类暂时不可用",error.message);}
}
$("annotations-list").addEventListener("submit",async event=>{
  event.preventDefault();const form=event.target,id=form.dataset.annotationId;if(!id)return;
  const data=new FormData(form),button=form.querySelector("button[type=submit]");button.disabled=true;
  try{await api(`api/task-annotations/${encodeURIComponent(id)}`,{method:"PATCH",body:{
    response_form:data.get("response_form"),coding_focus:data.get("coding_focus"),reason:data.get("reason"),
    expected_annotation_revision:annotationQueue.annotation_revision}});
    form.querySelector("input[type=checkbox]").disabled=false;notice("annotations-notice","草稿已保存，发布后查询才使用新标签。","info");
  }catch(error){notice("annotations-notice",error.message,"error");}finally{button.disabled=false;}
});
$("annotations-list").addEventListener("click",event=>{
  const button=event.target.closest("[data-annotation-source]");if(!button)return;
  const item=annotationQueue.items.find(x=>x.occurrence_id===button.dataset.annotationSource),span=item.source_spans?.[0] || {};
  showSource({revision_id:item.source_revision_id,start_line:span.start_line || 1,end_line:span.end_line || 1});
});
$("annotations-list").addEventListener("change",()=>{$("annotations-publish").disabled=!document.querySelector("[data-publish-id]:checked");});
$("annotations-refresh").addEventListener("click",()=>loadAnnotations());
$("annotations-next").addEventListener("click",()=>loadAnnotations(annotationQueue.next_cursor));
$("annotations-publish").addEventListener("click",async()=>{
  const occurrence_ids=Array.from(document.querySelectorAll("[data-publish-id]:checked"),node=>node.dataset.publishId);
  $("annotations-publish").disabled=true;
  try{const response=await api("api/task-annotations/publish",{method:"POST",body:{occurrence_ids,
    expected_annotation_revision:annotationQueue.annotation_revision}});
    notice("annotations-notice",`已发布 ${response.data.published} 条核验分类。`,"info");await loadAnnotations();
  }catch(error){notice("annotations-notice",error.message,"error");$("annotations-publish").disabled=false;}
});
async function search(cursor=null, structured=false, changedField=null,retryBody=null,restored=null) {
  const sequence = ++state.searchSequence;
  searchController?.abort(); searchController = new AbortController();
  const query = $("query").value.trim(), params = filters(), limit = Number($("result-limit").value), pipeline = $("search-pipeline").value;
  $("search-button").disabled = true; $("results").innerHTML = loading(query ? "正在检索题目，语义查询可能需要稍等…" : "正在读取高频题目…");
  $("query-cancel").hidden=false;
  $("results-title").textContent = query ? "检索结果" : "高频题目"; $("results-caption").textContent = "正在查询…";
  $("search-diagnostics").hidden = true; $("results-next").hidden = true; state.diagnostic = null; state.rows = []; notice("results-notice");
  try {
    let response;
    if(restored){
      response={data:restored.facts.data || [],meta:{...restored.facts.meta,planning:restored.planning,
        answer:restored.answer,conversation_id:restored.conversation_id,conversation_version:restored.conversation_version},
        warnings:restored.warnings || [],elapsed_ms:null};
    } else if(cursor || (!query) || (structured && state.listRequest)) {
      const changed = changedField ? {[changedField]:params[changedField] ?? null} : {};
      if(changedField === "topic_l1") changed.topic_l2 = null;
      const review=$("filter-review").value;
      const statuses=review==="UNMASTERED" ? ["UNSEEN","WEAK","REVIEWED"] : review ? [review] : [];
      const request = cursor ? {...state.listRequest,cursor} : {...(query ? state.listRequest : {}),...params,...changed,sort:$("browse-sort").value,page_size:limit,cursor:null,review_statuses:statuses,review_order:$("review-order").value};
      response = healthSnapshot?.query_router_enabled
        ? await listWithContext(request,searchController.signal,cursor)
        : await api("api/questions/list",{params:request,signal:searchController.signal});
      if (sequence !== state.searchSequence) return;
      state.listRequest = {...request,cursor:null};
    } else if(query && healthSnapshot?.query_router_enabled) {
      response = await agentQuery(retryBody || {message:query,filters:params,page_size:limit,pipeline,request_id:crypto.randomUUID(),conversation_id:state.libraryConversation?.id || null,expected_version:state.libraryConversation?.version ?? null},searchController.signal,"library",phase=>{
        if(sequence===state.searchSequence)$("results-caption").textContent=phase.stage==="tool" ? "正在查询题库…" : phase.stage==="tool_completed" ? "正在保存查询结果…" : "正在理解查询…";
      });
      if (sequence !== state.searchSequence) return;
      state.libraryConversation = {id:response.meta.conversation_id,version:response.meta.conversation_version};
      const plan=response.meta.planning?.spec;
      state.listRequest=plan && ["LIST","NEXT","STATS"].includes(plan.action) ? {...pickFilters(plan.filters),sort:plan.sort,top_n:plan.top_n,page_size:plan.page_size,review_statuses:plan.review_statuses || [],review_order:plan.review_order || "BEFORE_TOP_N",...(plan.action==="STATS" ? {group_by:plan.group_by} : {})} : null;
    } else {
      response = await api("api/questions/search",{params:{...params,query,pipeline,top_k:Math.min(50,limit)},signal:searchController.signal});
      if (sequence !== state.searchSequence) return;
      state.listRequest=null;
    }
    if (sequence !== state.searchSequence) return;
    if(response.meta.conversation_id) state.libraryConversation={id:response.meta.conversation_id,version:response.meta.conversation_version};
    if(state.libraryConversation)sessionStorage.setItem("libraryConversation",JSON.stringify(state.libraryConversation));
    state.nextCursor=response.meta.pagination?.next_cursor || null;
    $("results-next").hidden=!state.nextCursor;
    if(response.meta.pagination?.page_size) {
      const size=String(response.meta.pagination.page_size), selector=$("result-limit");
      if(!Array.from(selector.options).some(option=>option.value===size)) selector.add(new Option(`${size} 条`,size));
      selector.value=size;
    }
    if(response.meta.sort) $("browse-sort").value=response.meta.sort;
    const statuses=response.meta.review_statuses || [];
    $("filter-review").value=statuses.length===3 && !statuses.includes("MASTERED") ? "UNMASTERED" : statuses.length===1 ? statuses[0] : "";
    $("review-order").value=response.meta.review_order || "BEFORE_TOP_N";
    state.rows = response.data;
    state.diagnostic = {query,filters:pickFilters(response.meta.applied_filters || params),mode:query ? "search" : "browse",meta:response.meta,warnings:response.warnings,elapsed_ms:response.elapsed_ms,results:response.data.map(row => ({canonical_question_id:row.canonical_question_id,canonical_text:row.canonical_text,occurrence_count:row.occurrence_count,retrieval:row.retrieval}))};
    $("results").innerHTML = response.data.length ? response.data.map(resultRow).join("") : empty("没有找到符合条件的题目", query ? "试试核心关键词、调整筛选条件，或在导入页确认相关面经是否已生效。" : "当前筛选范围没有真实提问。可以放宽条件，或导入面经。");
    const total = response.meta.sample_counts?.occurrences, pagination=response.meta.pagination;
    $("results-title").textContent=response.meta.route==="CLARIFY" ? "需要补充查询条件" : response.meta.group_by && response.meta.group_by!=="question" ? "统计结果" : response.meta.route==="SQL" ? "题目列表" : "检索结果";
    const unit=response.meta.group_by && response.meta.group_by!=="question" ? "组" : "道题";
    const timing=restored ? "已恢复上次结果" : `${response.elapsed_ms} ms`;
    $("results-caption").textContent = pagination ? `${response.data.length} ${unit} · 范围 ${pagination.total} ${unit}${pagination.result_total!==pagination.total ? ` · 本集合 ${pagination.result_total} ${unit}` : ""}${pagination.top_n ? ` · 前 ${pagination.top_n} ${unit}` : ""} · ${labels.SQL} · ${timing}` : `${response.data.length} 条结果 · ${labels[response.meta.executed_pipeline || pipeline]} · ${timing}`;
    if(response.meta.route==="CLARIFY") notice("results-notice",response.meta.answer);
    if (response.warnings?.length) notice("results-notice",`本次执行有提示：${response.warnings.join("；")}`);
    $("diagnostics-content").textContent = pretty(state.diagnostic); $("search-diagnostics").hidden = false;
    await refreshWorkspaceForResult(response.meta);
    if (sequence !== state.searchSequence) return;
    const ids = response.data.map(row=>row.canonical_question_id).filter(Boolean);
    if (ids.length) {
      try { const reviews = await api("api/review/state", {params:{canonical_question_ids:ids.join(",")},signal:searchController.signal});
        if (sequence !== state.searchSequence) return;
        state.rows = state.rows.map(row=>({...row,user_status:reviews.data.states[row.canonical_question_id]?.status}));
        $("results").innerHTML = state.rows.map(resultRow).join("");
      } catch(error) { if(error.name !== "AbortError" && sequence === state.searchSequence) notice("results-notice",`题目已加载，复习状态暂时不可用：${error.message}`); }
    }
  } catch(error) {
    if(sequence===state.searchSequence){
      $("results-caption").textContent="";
      $("results").innerHTML=empty(error.name==="AbortError" ? "查询已取消" : "查询暂时没有完成",error.message)+
        (error.partial ? `<p>以下为已完成步骤的结果，可恢复本次查询继续处理。</p>${(error.partial.facts?.data || []).map(resultRow).join("")}` : "");
      if(error.name!=="AbortError"){notice("results-notice",error.message,"error");state.diagnostic={query,filters:params,pipeline,error:error.payload || error.message};$("diagnostics-content").textContent=pretty(state.diagnostic);$("search-diagnostics").hidden=false;}
    }
  }
  finally { if(sequence === state.searchSequence) {$("search-button").disabled=false;$("query-cancel").hidden=true;} }
}

async function showDetail(id, scope=state.diagnostic?.filters || filters(), origin=null) {
  if (!id) return;
  scope = pickFilters(scope);
  const sequence = ++state.detailSequence; state.detail=null;
  $("detail-content").innerHTML=loading("正在读取题目与来源…"); if(!$("detail-dialog").open) $("detail-dialog").showModal();
  try {
    const [detail,review]=await Promise.all([api(`api/questions/${encodeURIComponent(id)}`,{params:scope}),api("api/review/state",{params:{canonical_question_ids:id}})]);
    if(sequence!==state.detailSequence) return;
    if(detail.data.redirect_to_id) { await showDetail(detail.data.redirect_to_id,scope,origin); return; }
    const reviewState=review.data.states[id] || {};
    state.detail={...detail.data,filters:{...scope},meta:detail.meta,review_version:reviewState.version || 0,
      origin:origin || {query:state.diagnostic?.query || "",context:state.diagnostic || {filters:scope}}};
    const data=detail.data, current=review.data.states[id]?.status || "UNSEEN";
    const sourceHTML=(data.sources || []).map((source,index)=>{ const link=safeLink(source.source_url); return `<div class="source-card"><blockquote>${escapeHTML(source.quote)}</blockquote><div class="source-actions"><span>第 ${source.start_line}–${source.end_line} 行</span><button class="text-button" data-source-index="${index}">查看完整原文 ↗</button>${link ? `<a href="${escapeHTML(link)}" target="_blank" rel="noopener noreferrer">打开原帖 ↗</a>` : ""}</div></div>`; }).join("");
    const relations=(data.observed_followups || []).map(item=>`<li><button class="text-button" data-detail="${escapeHTML(item.canonical_question_id)}">${escapeHTML(item.canonical_text)}</button> · ${item.supporting_interview_count} 场面试有原文证据</li>`).join("");
    $("detail-content").innerHTML=`<h2 class="detail-title">${escapeHTML(data.canonical_text)}</h2><div class="question-meta"><span class="pill green">${escapeHTML(state.topicLabels[data.topic_id] || data.topic_id)}</span><span class="pill">${escapeHTML(labels[scope.coding_focus || data.question_type] || scope.coding_focus || data.question_type)}</span><span class="pill">${escapeHTML(labels[current])}</span></div><div class="detail-stats"><div><b>${data.occurrence_count}</b>真实提问</div><div><b>${data.interview_count}</b>面试场次</div><div><b>${data.source_document_count}</b>来源面经</div></div><div class="detail-section"><h3>原始问法 <span class="muted">${data.variants.length} 种</span></h3><ul class="variant-list">${data.variants.map(item=>`<li>${escapeHTML(item)}</li>`).join("") || "当前范围没有问法"}</ul></div><div class="detail-section"><h3>来源证据</h3>${sourceHTML || '<p class="muted">当前筛选范围没有来源。</p>'}${data.sources_total>data.sources.length ? `<p class="muted">先展示 ${data.sources.length} 条引用，共 ${data.sources_total} 条。</p>` : ""}</div>${relations ? `<div class="detail-section"><h3>真实追问</h3><ul class="variant-list">${relations}</ul></div>` : ""}<div class="detail-section"><h3>我的复习记录</h3><form id="review-form" class="review-form"><div class="review-fields"><label>复习状态<select id="review-status">${["UNSEEN","WEAK","REVIEWED","MASTERED"].map(status=>`<option value="${status}" ${current===status ? "selected" : ""}>${labels[status]}</option>`).join("")}</select></label><label>自评得分<select id="review-score"><option value="">不评分</option>${[0,1,2,3,4,5].map(score=>`<option value="${score}">${score} / 5</option>`).join("")}</select></label></div><label>笔记<textarea id="review-note" rows="2" maxlength="2000" placeholder="记录答题思路、遗漏点或下一步要补的内容"></textarea></label><div id="review-notice" class="notice" role="status" hidden></div><button class="button primary" id="review-button" type="submit">保存复习记录</button></form></div><div class="detail-foot"><span>题目编号 ${escapeHTML(id)}</span><button class="text-button" data-issue="${escapeHTML(id)}">⚑ 记录这道题的问题</button></div>`;
    $("review-form").addEventListener("submit",saveReview);
    $("review-note").value=reviewState.note || "";
    $("review-score").value=reviewState.last_score ?? "";
  } catch(error) { if(sequence===state.detailSequence) $("detail-content").innerHTML=empty("题目详情暂时不可用",error.message); }
}

async function saveReview(event) {
  event.preventDefault(); const detail=state.detail; if(!detail) return;
  const button=$("review-button"), score=$("review-score").value;
  const payload={idempotency_key:crypto.randomUUID(),items:[{canonical_question_id:detail.canonical_question_id,status:$("review-status").value,note:$("review-note").value,score:score==="" ? null : Number(score),expected_version:detail.review_version}]};
  button.disabled=true;
  try { await api("api/review",{method:"POST",body:payload}); if(state.detail===detail) { detail.review_version+=1; notice("review-notice","复习记录已保存。","info"); $("detail-content").querySelector(".question-meta").lastElementChild.textContent=labels[payload.items[0].status]; } toast("复习记录已保存"); if(state.page==="library") await search(); }
  catch(error) { if(state.detail?.canonical_question_id===detail.canonical_question_id) notice("review-notice",error.message,"error"); }
  finally { if(button.isConnected) button.disabled=false; }
}

async function showSource({revision_id,path,start_line=1,end_line=1}) {
  const sequence=++state.sourceSequence; $("source-title").textContent=path ? `本地原文 · ${path}` : "来源快照 · 原文"; $("source-caption").textContent="正在读取…"; $("source-content").innerHTML=loading(); if(!$("source-dialog").open) $("source-dialog").showModal();
  try {
    const response=await api(path ? "api/corpus/source" : `api/sources/${encodeURIComponent(revision_id)}`,{params:path ? {path} : {}});
    if(sequence!==state.sourceSequence) return;
    $("source-caption").textContent=path ? "本地文件内容。导入后的题目引用保存于不可变快照。" : `来源快照 · 引用位于第 ${start_line}–${end_line} 行`;
    $("source-content").innerHTML=response.data.markdown.split("\n").map((line,index)=>`<div class="source-line ${!path && index+1>=start_line && index+1<=end_line ? "highlight" : ""}"><span class="line-number">${index+1}</span><span class="line-text">${escapeHTML(line) || " "}</span></div>`).join("");
    $("source-content").querySelector(".highlight")?.scrollIntoView({block:"center"});
  } catch(error) { if(sequence===state.sourceSequence) $("source-content").innerHTML=empty("原文暂时不可用",error.message); }
}

async function sendChat(event,retryBody=null) {
  event?.preventDefault(); const message=retryBody?.message || $("chat-input").value.trim(); if(!message || state.chatBusy) return;
  state.chatBusy=true; $("chat-button").disabled=true; $("chat-input").value="";
  chatController=new AbortController();$("chat-cancel").hidden=false;
  const user=document.createElement("div"); user.className="chat-message user"; user.textContent=message; $("chat-messages").append(user);
  const reply=document.createElement("div"); reply.className="chat-message assistant"; reply.innerHTML=loading("正在查询真实面经…"); $("chat-messages").append(reply);
  try {
    const body=retryBody || {message,request_id:crypto.randomUUID(),conversation_id:state.chatConversation?.id || null,expected_version:state.chatConversation?.version ?? null};
    const response=healthSnapshot?.query_router_enabled ? await agentQuery(body,chatController.signal,"chat") :
      await api("api/agent/chat",{method:"POST",body,signal:chatController.signal}), data=response.data;
    if(data.conversation_id) state.chatConversation={id:data.conversation_id,version:data.conversation_version};
    const facts=data.facts || {}, rows=facts.data || facts.groups || facts.questions || (facts.canonical_question_id ? [facts] : []);
    reply.innerHTML=`<p>${escapeHTML(data.answer)}</p>${(facts.components || []).map(c=>`<details><summary>${escapeHTML(c.answer)}</summary>${(c.facts.data || []).map(resultRow).join("")}</details>`).join("")}${data.warnings?.length ? `<p class="muted">${escapeHTML(data.warnings.join("；"))}</p>` : ""}${Array.isArray(rows) ? rows.map(resultRow).join("") : ""}${facts.meta?.pagination?.next_cursor ? '<button class="button secondary chat-next">下一页 →</button>' : ""}<button class="text-button chat-report">⚑ 记录本次回答的问题</button><details><summary>查看回答依据与执行过程</summary><pre>${escapeHTML(pretty({intent:data.intent,planning:data.planning,tool_trace:data.tool_trace,meta:response.meta,facts:data.facts}))}</pre></details>`;
    const trace=data.tool_trace?.find(item=>["list_questions","get_question_stats","get_question_details","search_questions","query_question_stats","get_question_detail","get_topic_overview"].includes(item.name));
    const scope=pickFilters(trace?.parameters?.filters || trace?.parameters || {});
    const origin={query:message,context:{intent:data.intent,planning:data.planning,tool_trace:data.tool_trace,meta:response.meta,filters:scope,
      results:Array.isArray(rows) ? rows.slice(0,10).map(row=>({canonical_question_id:row.canonical_question_id,canonical_text:row.canonical_text})) : []}};
    reply.addEventListener("click",event=>{const button=event.target.closest("button");if(!button)return;
      if(button.dataset.detail){event.stopPropagation();showDetail(button.dataset.detail,scope,origin);}
      if(button.dataset.issue!==undefined){event.stopPropagation();openFeedback(button.dataset.issue,origin);}});
    reply.querySelector(".chat-report").addEventListener("click",()=>openFeedback(null,origin));
    reply.querySelector(".chat-next")?.addEventListener("click",()=>{$("chat-input").value="下一页";sendChat();});
    await refreshWorkspaceForResult(response.meta);
  } catch(error) { reply.innerHTML=empty(error.name==="AbortError" ? "查询已取消" : "本次问答未完成",error.message)+(error.partial ? `<p>以下为已完成步骤的结果，可恢复本次问答继续处理。</p>${(error.partial.facts?.data || []).map(resultRow).join("")}` : ""); }
  finally { state.chatBusy=false; $("chat-button").disabled=false;$("chat-cancel").hidden=true; }
}

let ingestSequence=0;
async function loadIngest() {
  const sequence=++ingestSequence;
  try {
    const [health,runs,documents]=await Promise.all([api("api/health"),api("api/ingest/runs"),api("api/corpus/documents")]);
    if(sequence!==ingestSequence) return;
    if($("ingest-notice").classList.contains("error")) notice("ingest-notice");
    renderHealth(health.data);
    $("ingest-runs").innerHTML=runs.data.length ? runs.data.slice(0,8).map(run=>{
      const complete=run.completed_documents ?? Math.min(run.total_documents,run.processed_documents+run.skipped_documents+run.failed_documents+run.needs_review_documents);
      const percent=run.total_documents ? Math.min(100,complete/run.total_documents*100) : (run.status==="SUCCEEDED" ? 100 : 0);
      const file=documents.data.find(item=>item.path===run.current_path), stage=file?.stage || run.current_stage;
      return `<article class="card run-card"><div class="run-title"><div><b>${run.mode==="reindex" ? "同步检索索引" : "面经导入"}</b><div class="run-time">${escapeHTML(timestamp(run.start_time))} · ${escapeHTML(run.run_id.slice(0,8))}</div></div><span class="pill ${["FAILED","PARTIAL_FAILURE"].includes(run.status) ? "orange" : "green"}">${escapeHTML(labels[run.status] || run.status)}</span></div><div class="progress"><div style="width:${percent}%"></div></div><div class="run-counts"><span>处理 ${complete} / ${run.total_documents} 篇</span><span>已生效 ${Math.max(0,run.processed_documents-run.excluded_documents)} 篇</span><span>已排除 ${run.excluded_documents} 篇</span><span>跳过 ${run.skipped_documents} 篇</span><span>失败 ${run.failed_documents} 篇</span><span>提取 ${run.processed_questions} 次提问</span></div>${run.current_path ? `<p class="muted">当前：${escapeHTML(run.current_path)}${stage ? ` · ${escapeHTML(labels[stage] || stage)}` : ""}</p>` : ""}${run.failed_paths.length || run.index_error || run.last_error ? `<div class="run-errors">${run.last_error ? `本批次最近失败：${escapeHTML(run.last_error)} ` : ""}${run.index_error ? `索引提示：${escapeHTML(run.index_error)}` : ""}${run.failed_paths.length ? `<details><summary>本批次 ${run.failed_paths.length} 篇失败记录</summary>${run.failed_paths.map(escapeHTML).join("<br>")}</details>` : ""}</div>` : ""}${["FAILED","PARTIAL_FAILURE"].includes(run.status) ? `<button class="text-button" data-retry="${escapeHTML(run.run_id)}">重试失败任务 ↗</button>` : ""}</article>`;
    }).join("") : empty("还没有导入任务","选择下方文件，或导入整个目录中的新增和修改文件。");
    state.documents=documents.data; renderDocuments();
  } catch(error) { if(sequence===ingestSequence) notice("ingest-notice",error.message,"error"); }
}

function renderDocuments() {
  const query=$("document-query").value.trim().toLocaleLowerCase(), status=$("document-status").value;
  const rows=state.documents.filter(item=>(!query || item.path.toLocaleLowerCase().includes(query)) && window.InterviewWorkspace.matchesDocumentStatus(item,status));
  $("document-caption").textContent=`共 ${state.documents.length} 篇 · 当前显示 ${rows.length} 篇`;
  $("document-list").innerHTML=rows.length ? rows.map(item=>`<div class="document-row"><input type="checkbox" data-document="${escapeHTML(item.path)}" aria-label="选择 ${escapeHTML(item.title)}" ${state.selected.has(item.path) ? "checked" : ""}><span class="document-name">${escapeHTML(item.title)}${item.status==="RUNNING" && item.stage ? `<small class="muted"> · ${escapeHTML(labels[item.stage] || item.stage)}</small>` : ""}${item.active_status && item.active_status!==item.status ? `<small class="muted"> · 当前版本${escapeHTML(labels[item.active_status] || item.active_status)}</small>` : ""}${item.exclusion_reason ? `<small class="muted"> · ${escapeHTML(item.exclusion_reason)}</small>` : ""}${item.error_code ? `<small class="muted"> · ${item.status==="FAILED" ? "失败原因" : "历史失败"}：${escapeHTML(item.error_detail || item.error_code)}</small>` : ""}</span><span class="pill ${item.status==="INCLUDED" ? "green" : ["PENDING","FAILED","NEEDS_REVIEW"].includes(item.status) ? "orange" : ""}">${escapeHTML(labels[item.status] || item.status)}</span><button class="text-button" data-local-source="${escapeHTML(item.path)}">查看原文 ↗</button></div>`).join("") : empty("没有匹配的文件","试试其他文件名，或放宽状态筛选。");
  $("ingest-selected").disabled=!state.selected.size || state.ingestBusy;
  $("ingest-selected").textContent=state.selected.size ? `导入所选 ${state.selected.size} 篇` : "导入所选文件";
}

async function startIngest(mode,paths,runId) {
  if(state.ingestBusy) return; state.ingestBusy=true;
  ["ingest-changed","reindex","ingest-selected"].forEach(id=>$(id).disabled=true);
  notice("ingest-notice","正在提交后台任务…","info");
  try {
    const body={idempotency_key:crypto.randomUUID(),...(runId ? {} : {mode,...(paths ? {paths} : {})})};
    const response=await api(runId ? `api/ingest/runs/${encodeURIComponent(runId)}/retry-failed` : "api/ingest",{method:"POST",body});
    notice("ingest-notice",`任务已提交：${response.data.run_id.slice(0,8)}。可在下方查看进度。`,"info");
    state.selected.clear(); await loadIngest();
  } catch(error) { notice("ingest-notice",error.message,"error"); }
  finally { state.ingestBusy=false; ["ingest-changed","reindex"].forEach(id=>$(id).disabled=false); renderDocuments(); }
}

function openFeedback(questionId=null, supplied=null) {
  const row=state.detail?.canonical_question_id===questionId ? state.detail : state.rows.find(item=>item.canonical_question_id===questionId);
  const origin=supplied || (row===state.detail ? state.detail?.origin : null) || {query:state.diagnostic?.query || $("query").value.trim(),context:state.diagnostic || {}};
  state.feedbackContext={...origin,canonical_question_id:questionId || null,context:{...origin.context,
    question:row ? {canonical_question_id:row.canonical_question_id,canonical_text:row.canonical_text,topic_id:row.topic_id,retrieval:row.retrieval} : null}};
  $("feedback-note").value=""; notice("feedback-notice");
  $("feedback-context-caption").textContent=`查询：${state.feedbackContext.query || "无特定查询"}${questionId ? `\n题目：${row?.canonical_text || questionId}` : ""}`;
  if(!$("feedback-dialog").open) $("feedback-dialog").showModal(); $("feedback-note").focus();
}

async function saveFeedback(event) {
  event.preventDefault(); const button=$("save-feedback"); button.disabled=true;
  const context=state.feedbackContext || {};
  try {
    await api("api/feedback",{method:"POST",body:{category:$("feedback-category").value,note:$("feedback-note").value.trim(),query:context.query || "",canonical_question_id:context.canonical_question_id || null,context:context.context || {}}});
    $("feedback-dialog").close(); toast("问题已保存，可在「问题记录」中查看"); if(state.page==="feedback") await loadFeedback();
  } catch(error) { notice("feedback-notice",error.message,"error"); }
  finally { button.disabled=false; }
}

async function loadFeedback() {
  $("feedback-list").innerHTML=loading("正在读取问题记录…");
  try {
    const response=await api("api/feedback",{params:{limit:200}}); state.feedback=response.data;
    $("feedback-list").innerHTML=state.feedback.length ? state.feedback.map(item=>`<article class="card feedback-card"><div class="run-title"><span class="pill orange">${escapeHTML(labels[item.category] || item.category)}</span><span class="run-time">${escapeHTML(timestamp(item.created_at))}</span></div><h3>${escapeHTML(item.note)}</h3>${item.query ? `<p>当时的查询：${escapeHTML(item.query)}</p>` : ""}${item.canonical_question_id ? `<button class="text-button" data-detail="${escapeHTML(item.canonical_question_id)}" data-feedback-id="${escapeHTML(item.id)}">打开对应题目 ↗</button>` : ""}<details class="advanced"><summary>查看保存的上下文</summary><pre>${escapeHTML(pretty(item.context))}</pre></details></article>`).join("") : empty("还没有问题记录","检索或查看题目时，点击旗帜按钮就能把问题和上下文一起留下来。");
  } catch(error) { $("feedback-list").innerHTML=empty("问题记录暂时不可用",error.message); }
}

function navigate(page) {
  if(!["library","chat","ingest","feedback","preferences","annotations"].includes(page)) page="library"; state.page=page;
  document.querySelectorAll(".page").forEach(node=>node.hidden=node.id!==`page-${page}`);
  document.querySelectorAll(".nav-item").forEach(node=>{node.classList.toggle("active",node.dataset.page===page);node.setAttribute("aria-current",node.dataset.page===page ? "page" : "false");});
  $("breadcrumb-page").textContent={library:"题库与检索",chat:"面经问答",ingest:"导入与状态",feedback:"问题记录",preferences:"查询偏好",annotations:"分类核验"}[page];
  history.replaceState(null,"",`#${page}`);
  if(page==="ingest") loadIngest(); if(page==="feedback") loadFeedback();
  if(page==="preferences")loadPreferences();
  if(page==="annotations")loadAnnotations();
}

document.addEventListener("click",event=>{
  const button=event.target.closest("button"); if(!button) return;
  if(button.dataset.page) navigate(button.dataset.page);
  if(button.dataset.close) $(button.dataset.close).close();
  if(button.dataset.detail){
    const record=button.dataset.feedbackId ? state.feedback.find(item=>item.id===button.dataset.feedbackId) : null;
    showDetail(button.dataset.detail,record ? record.context?.filters || {} : button.closest("#detail-dialog") ? state.detail?.filters : state.diagnostic?.filters || filters(),
      record ? {query:record.query,context:record.context} : button.closest("#detail-dialog") ? state.detail?.origin : null);
  }
  if(button.dataset.issue !== undefined) openFeedback(button.dataset.issue || null);
  if(button.dataset.sourceIndex !== undefined) showSource(state.detail.sources[Number(button.dataset.sourceIndex)]);
  if(button.dataset.localSource) showSource({path:button.dataset.localSource});
  if(button.dataset.retry) startIngest(null,null,button.dataset.retry);
  if(button.dataset.query) { $("query").value=button.dataset.query; search(); }
  if(button.dataset.chat) { $("chat-input").value=button.dataset.chat; sendChat(); }
});
document.addEventListener("change",event=>{if(event.target.dataset.document) { const path=event.target.dataset.document; event.target.checked ? state.selected.add(path) : state.selected.delete(path); renderDocuments(); }});
$("search-form").addEventListener("submit",event=>{event.preventDefault();search();});
$("filter-topic").addEventListener("change",()=>{subtopicOptions();search(null,true,"topic_l1");});
const filterControls={"filter-subtopic":"topic_l2","filter-company":"company","filter-round":"round","filter-type":"question_type","filter-coding":"coding_focus","filter-response":"response_form","filter-annotation":"annotation_status"};
[...Object.keys(filterControls),"search-pipeline","browse-sort","result-limit"].forEach(id=>$(id).addEventListener("change",()=>search(null,true,filterControls[id] || null)));
$("results-next").addEventListener("click",()=>search(state.nextCursor));
$("chat-reset").addEventListener("click",()=>{chatController?.abort();state.chatConversation=null;sessionStorage.removeItem("chatConversation");sessionStorage.removeItem("chatPending");$("chat-retry").hidden=true;$("chat-messages").replaceChildren();toast("已开始新会话");});
$("browse-button").addEventListener("click",()=>{$("query").value="";search();});
$("reset-filters").addEventListener("click",()=>{["filter-topic","filter-company","filter-round","filter-type","filter-coding","filter-response","filter-annotation","filter-review"].forEach(id=>$(id).value="");state.listRequest=null;state.libraryConversation=null;sessionStorage.removeItem("libraryConversation");subtopicOptions();search();});
$("chat-form").addEventListener("submit",sendChat);
$("chat-input").addEventListener("keydown",event=>{if(event.key==="Enter" && !event.shiftKey && !event.isComposing){event.preventDefault();sendChat();}});
$("feedback-form").addEventListener("submit",saveFeedback);
["open-general-feedback","report-results","new-feedback"].forEach(id=>$(id).addEventListener("click",()=>openFeedback()));
$("refresh-feedback").addEventListener("click",loadFeedback);
$("refresh-workspace").addEventListener("click",()=>{refreshWorkspace();if(state.page==="ingest")loadIngest();else if(state.page==="library")search();});
$("refresh-ingest").addEventListener("click",()=>{loadIngest();refreshWorkspace();});
$("ingest-changed").addEventListener("click",()=>startIngest("changed"));
$("reindex").addEventListener("click",()=>startIngest("reindex",[]));
$("ingest-selected").addEventListener("click",()=>startIngest("changed",Array.from(state.selected)));
$("document-query").addEventListener("input",renderDocuments);$("document-status").addEventListener("change",renderDocuments);
$("copy-diagnostics").addEventListener("click",async()=>{try{await navigator.clipboard.writeText(pretty(state.diagnostic));toast("排查信息已复制");}catch{$("search-diagnostics").open=true;const selection=window.getSelection();const range=document.createRange();range.selectNodeContents($("diagnostics-content"));selection.removeAllRanges();selection.addRange(range);toast("请复制已选中的排查信息");}});
$("export-feedback").addEventListener("click",()=>{const url=URL.createObjectURL(new Blob([pretty(state.feedback)],{type:"application/json;charset=utf-8"}));const link=document.createElement("a");link.href=url;link.download=`面经问题记录-${new Date().toLocaleDateString("sv-SE")}.json`;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);});
window.addEventListener("hashchange",()=>navigate(location.hash.slice(1)));
$("query-cancel").addEventListener("click",()=>searchController?.abort());
$("chat-cancel").addEventListener("click",()=>chatController?.abort());
$("query-retry").addEventListener("click",()=>{const body=JSON.parse(sessionStorage.getItem("libraryPending") || "null");if(body){$("query").value=body.message;search(null,false,null,body);}});
$("chat-retry").addEventListener("click",()=>{const body=JSON.parse(sessionStorage.getItem("chatPending") || "null");if(body)sendChat(null,body);});
$("preference-key").addEventListener("change",preferenceChoices);
$("preferences-form").addEventListener("submit",async event=>{
  event.preventDefault();const key=$("preference-key").value;
  try {
    const current=await api(`api/preferences/${key}`);
    const raw=$("preference-value").value;
    await api(`api/preferences/${key}`,{method:"PATCH",body:{value:key==="page_size" ? Number(raw) : raw,
      expected_version:current.data.version,source_message:"用户在查询偏好页明确保存"}});
    notice("preferences-notice","长期偏好已保存；明确的本次查询仍优先。","info");
    await loadPreferences();
  }catch(error){notice("preferences-notice",error.message,"error");}
});
$("preferences-list").addEventListener("click",async event=>{
  const button=event.target.closest("[data-delete-preference]");if(!button)return;
  try{await api(`api/preferences/${button.dataset.deletePreference}`,{method:"DELETE",
    params:{expected_version:Number(button.dataset.version)}});await loadPreferences();notice("preferences-notice","偏好已删除。","info");}
  catch(error){notice("preferences-notice",error.message,"error");}
});
["filter-review","review-order"].forEach(id=>$(id).addEventListener("change",()=>search(null,true)));
setInterval(()=>{if(!document.hidden && state.page==="ingest" && !state.ingestBusy)loadIngest();},12000);

async function init(){
  let restored=null,restoredFilters=null;
  for(const key of ["libraryConversation","chatConversation"]){
    try{
      const saved=JSON.parse(sessionStorage.getItem(key) || "null");if(!saved?.id)continue;
      const response=await api(`api/conversations/${encodeURIComponent(saved.id)}`),c=response.data;
      state[key]={id:c.conversation_id,version:c.version};
      if(key==="libraryConversation"){
        const last=c.turns.filter(t=>t.status==="SUCCEEDED").at(-1);
        if(last){restored=last.result;$("query").value=c.turns.filter(t=>t.message!=="结构化列表操作").at(-1)?.message || "";restoredFilters=c.state.filters || {};
          state.listRequest=c.state.list_request ? {...c.state.list_request,cursor:null} : null;}
      }
      if(key==="chatConversation")$("chat-messages").innerHTML=c.turns.map(t=>`<div class="chat-message user">${escapeHTML(t.message)}</div><div class="chat-message assistant"><p>${escapeHTML(t.status==="SUCCEEDED" ? t.result.answer : "本次问答尚未完成，可恢复原请求。")}</p>${(t.result?.facts?.data || []).map(resultRow).join("")}</div>`).join("");
    }catch{sessionStorage.removeItem(key);}
  }
  for(const kind of ["library","chat"])$(kind==="library" ? "query-retry" : "chat-retry").hidden=!sessionStorage.getItem(`${kind}Pending`);
  navigate(location.hash.slice(1));
  try{const topics=await api("api/topics");state.taxonomy=topics.data.topics;for(const [l1,children] of Object.entries(state.taxonomy))for(const [l2,id] of Object.entries(children))state.topicLabels[id]=`${l1} / ${l2}`;topicOptions();}catch(error){notice("global-message",error.message,"error");}
  await refreshWorkspace();
  if(!restored){
    try{
      const response=await api("api/preferences"),prefs=Object.fromEntries(response.data.filter(p=>p.active).map(p=>[p.key,p.value]));
      if(prefs.page_size){const size=String(prefs.page_size),selector=$("result-limit");if(!Array.from(selector.options).some(o=>o.value===size))selector.add(new Option(`${size} 条`,size));selector.value=size;}
      if(prefs.pipeline)$("search-pipeline").value=prefs.pipeline;
    }catch{}
  }
  if(restoredFilters){
    $("filter-topic").value=restoredFilters.topic_l1 || "";subtopicOptions();
    const controls={"filter-topic":"topic_l1",...filterControls};
    for(const [id,key] of Object.entries(controls))$(id).value=restoredFilters[key] || "";
  }
  await search(null,false,null,null,restored);
}
init();
