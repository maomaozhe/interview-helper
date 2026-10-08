(function (root) {
  "use strict";
  const filterKeys = ["company", "position", "job_family", "language", "topic_l1", "topic_l2",
    "question_type", "response_form", "coding_focus", "annotation_status", "round", "start_date", "end_date", "date_basis"];
  function pickFilters(source) {
    return Object.fromEntries(filterKeys.filter(key => source?.[key] !== undefined
      && source[key] !== null && source[key] !== "").map(key => [key, source[key]]));
  }
  function selectHealthSnapshot(current, incoming) {
    if (!current) return incoming;
    if (incoming.current_revision < current.current_revision ||
        (incoming.current_revision === current.current_revision &&
         incoming.indexed_revision < current.indexed_revision)) return current;
    return incoming;
  }
  function shouldRefreshWorkspace(current, resultMeta) {
    if (!Number.isInteger(resultMeta?.corpus_revision)) return false;
    return !current || resultMeta.corpus_revision > current.current_revision ||
      current.indexed_revision < current.current_revision;
  }
  function matchesDocumentStatus(item, filter) {
    if (!filter) return true;
    const active = item.active_status || (["INCLUDED", "EXCLUDED"].includes(item.status) ? item.status : null);
    if (["INCLUDED", "EXCLUDED"].includes(filter)) return active === filter;
    if (filter === "PENDING") return active === null;
    if (filter === "CHANGED") return item.content_changed === true || item.status === "CHANGED";
    return item.status === filter;
  }
  function feedbackOrigin(message, result, kind="chat") {
    const meta=result.facts?.meta || result.meta || {};
    return {query:message,kind,context:{run_id:result.run_id || meta.run_id,
      request_id:meta.request_id,conversation_id:result.conversation_id || meta.conversation_id,
      answer:result.answer || meta.answer,filters:pickFilters(meta.applied_filters),
      planning:result.planning || meta.planning,
      meta:{request_id:meta.request_id,corpus_revision:meta.corpus_revision,
        task_annotation_revision:meta.task_annotation_revision,executed_pipeline:meta.executed_pipeline,
        relevance_status:meta.relevance_status}}};
  }
  function requeryBody(message, conversation, feedbackIds=[], origin=null) {
    return {message,conversation_id:conversation?.id || null,expected_version:conversation?.version ?? null,
      feedback_ids:feedbackIds,...(origin?.context?.run_id ? {requery_of_run_id:origin.context.run_id} : {})};
  }
  function clarificationFor(result) {
    const meta=result?.facts?.meta || result?.meta || {};
    const plan=result?.planning?.spec || meta.planning?.spec || {};
    const clarification=meta.clarification || {};
    if(!clarification.question && plan.action!=="CLARIFY")return null;
    const choices=Array.isArray(clarification.options) && clarification.options.length ? clarification.options : plan.clarification_options;
    return {question:clarification.question || plan.clarification || result.answer || "请补充你的需求。",
      options:[...new Set((Array.isArray(choices) ? choices : []).filter(x=>typeof x==="string" && x.trim()).map(x=>x.trim()))].slice(0,4)};
  }
  function queryStage(event) {
    if(event.type==="tool_result")return {key:"presenting",label:"正在呈现结果"};
    if(event.type!=="stage" && event.type!=="accepted")return null;
    const stage=event.type==="accepted" ? "accepted" : event.stage;
    const labels={accepted:"已收到问题",waiting_for_model:"等待模型处理",planning:"正在理解问题与上下文",
      eligibility:"正在核对筛选范围",embedding:"正在理解检索语义",retrieving:"正在检索题库",
      reranking:"正在核对题目相关性",assembling:"正在整理题目与来源",cancel_requested:"正在停止"};
    if(stage==="tool") {
      const actions={CLARIFY:"正在准备可选方向",LIST:"正在查询题目列表",NEXT:"正在读取下一页",
        STATS:"正在统计题目",DETAILS:"正在读取题目与来源",REVIEW_STATE:"正在读取复习状态",RECORD_REVIEW:"正在保存复习记录"};
      return event.action==="SEARCH" ? null : {key:event.action || event.tool,label:actions[event.action] || "正在执行查询"};
    }
    if(!labels[stage])return null;
    return {key:stage,label:labels[stage]+(stage==="reranking" && Number.isInteger(event.count) ? ` · ${event.count} 条候选` : "")};
  }
  function answerContinuation(result) {
    const meta=result?.facts?.meta || result?.meta || {};
    const run_id=result?.run_id || meta.run_id,conversation_id=result?.conversation_id || meta.conversation_id;
    const version=result?.conversation_version ?? meta.conversation_version;
    if(!run_id || !conversation_id || !Number.isInteger(version))return null;
    return {run_id,conversation_id,version,clarification:clarificationFor(result)?.question || null,
      next:meta.pagination?.next_cursor || null};
  }
  function historyContinuation(history) {
    const latest=history?.turns?.at(-1);
    // A failed or running follow-up is still a newer turn. Its older buttons
    // must not silently submit against the conversation's retained state.
    if(latest?.status!=="SUCCEEDED")return null;
    const continuation=answerContinuation({...latest.result,run_id:latest.run_id,
      conversation_id:history.conversation_id,conversation_version:history.version});
    if(!continuation)return null;
    if(continuation.clarification!==history.state?.pending_clarification?.question)continuation.clarification=null;
    if(continuation.next!==history.state?.list_request?.cursor)continuation.next=null;
    return continuation;
  }
  function canContinueAnswer(origin, conversation, continuation, action, busy=false) {
    return !busy && ["clarification","next"].includes(action) && Boolean(continuation?.[action]) &&
      Boolean(origin?.context?.run_id) && origin.context.run_id===continuation.run_id &&
      origin.context.conversation_id===continuation.conversation_id &&
      conversation?.id===continuation.conversation_id && conversation.version===continuation.version;
  }
  const api = {pickFilters, selectHealthSnapshot, shouldRefreshWorkspace, matchesDocumentStatus, feedbackOrigin,
    requeryBody,clarificationFor,queryStage,answerContinuation,historyContinuation,canContinueAnswer};
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.InterviewWorkspace = api;
})(typeof window !== "undefined" ? window : this);
