import {Agent} from "@earendil-works/pi-agent-core";
import {createModels, createProvider, envApiKeyAuth, Type} from "@earendil-works/pi-ai";
import {openAICompletionsApi} from "@earendil-works/pi-ai/api/openai-completions.lazy";

// BEGIN GENERATED TOOL CONTRACT (python scripts/sync-tool-contract.py)
const toolContract = {
  common_required: ["action", "filters", "sort", "top_n", "page_size"],
  tools: {
    "list_questions": {"actions": ["LIST", "NEXT", "CLARIFY"], "pi_order": 0, "fields": ["semantic_task", "final", "clarification", "clarification_options", "review_statuses", "review_order", "user_goal"], "required": [], "hint": " Use LIST when the user requests a question list. If the current user goal is COUNT and the message only corrects or restates its subject, continue COUNT even if the previous count already used that subject; do not infer a new list request. For LIST without an explicit requested N, set top_n=null and use page_size for one page. Never infer Top 20 from a default page_size of 20; keep the complete scope pageable. Broad algorithm lists use semantic_task=ALGORITHM_PROBLEM (LIST); this is a complete evidence-audited SQL collection, not semantic retrieval Top50. All engineering/system-design questions use semantic_task=ENGINEERING_DESIGN with LIST and top_n=null unless an N is explicit; preserve original user scope, including AI/Agent engineering tasks. NEXT preserves the current scope and pages either the audited SQL collection or the host-stored bounded semantic candidate result; semantic candidates are never a corpus-complete set. Missing or unresolved source audits cannot be presented as a complete set."},
    "search_questions": {"actions": ["SEARCH"], "pi_order": 1, "fields": ["final", "search_query", "relevance_query", "lexical_facets", "preferred_question_type", "pipeline", "user_goal"], "required": ["search_query", "pipeline"], "hint": " SEARCH requires top_n=null; use page_size (1–50) for the requested result count.", "replace_properties": {"top_n": {"type": "null"}}, "merge_properties": {"page_size": {"type": "integer", "minimum": 1, "maximum": 50}}},
    "get_question_stats": {"actions": ["STATS"], "pi_order": 2, "fields": ["semantic_task", "final", "group_by", "topic_level", "review_statuses", "review_order", "user_goal"], "required": ["group_by"], "hint": " Group authoritative counts by topic, company or round. Use topic_level=L2 for a breakdown within one L1 topic such as Redis; use L1 for broad topic distribution."},
    "get_question_count": {"actions": ["COUNT"], "pi_order": 4, "fields": ["semantic_task", "final", "review_statuses", "user_goal"], "required": [], "hint": " Count the complete current discussion scope within filters, not a result page. A bare total question inherits the current topic; only an explicit whole-corpus request clears it. When the current goal is COUNT, subject corrections or restatements remain COUNT even after an already-correct count. One COUNT returns both counts.canonical_questions (distinct questions) and counts.occurrences (appearances). For a direct total question, including asking both numbers, call only this tool once with final=true; do not call STATS for appearances or ANSWER to restate counts for a DIRECT_RESULT goal. For a SYNTHESIS user_goal, this is evidence only: continue with further authorized reads as needed, then ANSWER. Interpret the conversational objective independently of the tool action.", "replace_properties": {"top_n": {"type": "null"}}},
    "get_question_details": {"actions": ["DETAILS"], "pi_order": 3, "fields": ["final", "question_ids", "scope", "user_goal"], "required": ["question_ids"], "hint": ""},
    "answer_question": {"actions": ["ANSWER"], "pi_order": 5, "fields": ["final", "answer_text", "answer_kind", "answer_basis", "question_ids", "evidence_tool_ordinals", "user_goal"], "required": ["answer_text"], "hint": " Finish with a direct explanation, comparison, solution, study plan or conversation response. Set final=true and identify the knowledge basis. Cite only question_ids returned by host tools; corpus sources establish interview questions, not the correctness of a generated answer.", "pi_required_extra": ["answer_kind", "answer_basis", "final"], "replace_properties": {"final": {"type": "boolean", "const": true}}, "merge_existing_properties": {"question_ids": {"maxItems": 10}}},
    "get_review_state": {"actions": ["REVIEW_STATE"], "pi_order": 6, "fields": ["final", "question_ids", "scope", "user_goal"], "required": ["question_ids"], "hint": ""},
    "record_review": {"actions": ["RECORD_REVIEW"], "pi_order": 7, "fields": ["final", "review_items", "scope", "user_goal"], "required": ["review_items"], "hint": ""}
  }
};
// END GENERATED TOOL CONTRACT
const toolsByAction = Object.fromEntries(Object.entries(toolContract.tools)
  .sort(([,a],[,b]) => a.pi_order-b.pi_order).map(([name,tool]) => [name,tool.actions]));

// BEGIN GENERATED EVIDENCE PROJECTION (python scripts/sync-tool-contract.py)
const evidenceProjection = {
  row_fields: ["canonical_question_id", "key", "occurrence_count", "interview_count", "source_document_count", "company_count", "semantic_verification", "confirmed_occurrence_count", "pending_occurrence_count", "relevance_scope_status", "relevance_scope_reason", "relevance_scope_hash", "frequency_basis"],
  source_fields: ["revision_id", "start_line", "end_line", "quote"],
  meta_fields: {"host": ["route", "corpus_revision", "task_annotation_revision", "user_state_revision", "applied_filters", "group_by", "topic_level", "sort", "counts", "sample_counts", "total_groups", "count_scope", "relevance_scope_hash", "scope_candidate_counts", "candidate_pool_limit", "candidate_workset_progress", "candidate_count", "eligible_count", "returned_count", "invalid_candidate_count", "invalid_evidence_count", "invalid_schema_count", "candidate_verification_status", "verification_basis", "result_completeness", "search_ranking", "frequency_ranking", "semantic_scope", "semantic_counts", "pending_counts"], "pi_legacy": ["route", "corpus_revision", "task_annotation_revision", "user_state_revision", "applied_filters", "group_by", "topic_level", "sort", "counts", "sample_counts", "total_groups", "count_scope", "relevance_scope_hash", "scope_candidate_counts", "candidate_pool_limit", "candidate_workset_progress", "review_statuses", "review_order", "relevance_status", "returned_count", "semantic_task", "semantic_scope", "semantic_counts", "pending_counts", "candidate_count", "eligible_count", "invalid_candidate_count", "invalid_evidence_count", "invalid_schema_count", "candidate_verification_status", "verification_basis", "result_completeness"]},
  pagination_fields: ["total", "result_total", "returned", "offset", "page_size", "top_n", "kind", "workset", "processing_complete", "has_unprocessed", "next_requires_processing"],
  pagination_boolean_fields: ["workset", "processing_complete", "has_unprocessed", "next_requires_processing"],
  workset_progress: {"version": "candidate_workset_progress_v1", "count_fields": ["candidate_total", "processed", "unprocessed", "confirmed", "pending", "excluded", "visible", "batch_size", "last_batch_count"], "boolean_fields": ["processing_complete", "next_requires_processing"]},
  coverage_evidence: {"count_fields": ["candidate_count", "eligible_count", "returned_count", "invalid_candidate_count", "invalid_evidence_count", "invalid_schema_count"], "enum_fields": {"candidate_verification_status": ["COMPLETE", "PARTIAL"], "verification_basis": ["empty_sql_scope"]}, "max_count": 9007199254740991, "result_completeness": {"count_fields": ["candidate_count", "eligible_count", "returned_count", "invalid_candidate_count", "invalid_evidence_count", "invalid_schema_count", "page_cutoff_count", "processed_count", "unprocessed_count", "confirmed_count", "pending_count", "excluded_count"], "boolean_fields": ["corpus_complete", "verification_complete", "candidate_universe_complete", "business_count_complete", "complete", "business_membership_complete"], "enum_fields": {"scope": ["retrieval_candidates", "empty_sql_scope", "engineering_candidate_workset"], "reason": ["candidate_workset_unprocessed", "candidate_workset_pending", "complete_authorized_candidate_workset"]}}},
  review_states: {"scope": "requested_question_ids", "statuses": ["UNSEEN", "WEAK", "REVIEWED", "MASTERED"], "count_fields": ["review_count", "version"], "binding_statuses": ["RESOLVED"], "id_pattern": "[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", "max_input_items": 1000, "max_items": 5, "max_score": 5},
  limits: {"canonical_text": 400, "quote": 160, "sources": 2, "relevance_scope_reason": 400, "tool_rows": 5}
};
// END GENERATED EVIDENCE PROJECTION

function inlineSchema(value, definitions) {
  if (Array.isArray(value)) return value.map(v => inlineSchema(v, definitions));
  if (value === null || typeof value !== "object") return value;
  if (value.$ref?.startsWith("#/$defs/")) return inlineSchema(definitions[value.$ref.slice(8)], definitions);
  return Object.fromEntries(Object.entries(value).filter(([k]) => k !== "$defs")
    .map(([k,v]) => [k, inlineSchema(v, definitions)]));
}

// Only annotation keywords are removed. Property names and every validation
// keyword (including the host's conditional semantic-scope rules) survive.
function compactSchema(value) {
  if (value === null || typeof value !== "object") return value;
  return Object.fromEntries(Object.entries(value)
    .filter(([key]) => !["title", "default"].includes(key))
    .map(([key, item]) => {
      if (["properties", "patternProperties", "$defs", "dependentSchemas"].includes(key))
        return [key,Object.fromEntries(Object.entries(item).map(([name,child]) => [name,compactSchema(child)]))];
      if (["allOf", "anyOf", "oneOf", "prefixItems"].includes(key) || key === "items" && Array.isArray(item))
        return [key,item.map(child => compactSchema(child))];
      if (["items", "additionalItems", "additionalProperties", "unevaluatedProperties", "unevaluatedItems",
        "contains", "propertyNames", "not", "if", "then", "else"].includes(key))
        return [key,compactSchema(item)];
      // Values such as const/enum are literal data, not nested schemas.
      return [key,item];
    }));
}

function evidenceRow(row) {
  const projected=Object.fromEntries(evidenceProjection.row_fields.filter(key=>row[key]!==undefined).map(key=>[key,row[key]]));
  if(projected.frequency_basis!=="engineering_qualified_occurrences")delete projected.frequency_basis;
  if(typeof projected.relevance_scope_reason==="string")projected.relevance_scope_reason=projected.relevance_scope_reason.slice(0,evidenceProjection.limits.relevance_scope_reason);
  else delete projected.relevance_scope_reason;
  if(typeof row.canonical_text==="string")projected.canonical_text=row.canonical_text.slice(0,evidenceProjection.limits.canonical_text);
  if(Array.isArray(row.sources))projected.sources=row.sources.slice(0,evidenceProjection.limits.sources).map(source=>
    Object.fromEntries(evidenceProjection.source_fields.filter(key=>source[key]!==undefined)
      .map(key=>[key,key==="quote" ? String(source[key]).slice(0,evidenceProjection.limits.quote) : source[key]])));
  return projected;
}

function worksetProgress(progress) {
  const spec=evidenceProjection.workset_progress;
  if(!progress || typeof progress!=="object" || progress.version!==spec.version ||
    spec.count_fields.some(key=>!Number.isSafeInteger(progress[key]) || progress[key]<0) ||
    spec.boolean_fields.some(key=>typeof progress[key]!=="boolean"))return null;
  if(progress.processed+progress.unprocessed!==progress.candidate_total ||
    progress.confirmed+progress.pending+progress.excluded!==progress.processed ||
    progress.confirmed+progress.pending!==progress.visible || progress.batch_size<1 || progress.batch_size>50 ||
    progress.last_batch_count>progress.batch_size || progress.processing_complete!==(progress.unprocessed===0) ||
    progress.next_requires_processing && progress.processing_complete)return null;
  return {version:spec.version,...Object.fromEntries([...spec.count_fields,...spec.boolean_fields].map(key=>[key,progress[key]]))};
}

function coverageFields(value, spec) {
  if(!value || typeof value!=="object" || Array.isArray(value))return {};
  const projected={};
  for(const key of spec.count_fields)
    if(Number.isSafeInteger(value[key]) && value[key]>=0 && value[key]<=evidenceProjection.coverage_evidence.max_count)
      projected[key]=value[key];
  for(const key of spec.boolean_fields || [])
    if(typeof value[key]==="boolean")projected[key]=value[key];
  for(const [key,allowed] of Object.entries(spec.enum_fields))
    if(typeof value[key]==="string" && allowed.includes(value[key]))projected[key]=value[key];
  return projected;
}

function reviewStates(value) {
  const spec=evidenceProjection.review_states;
  if(!spec || !value || typeof value!=="object" || Array.isArray(value))return undefined;
  const wrapped=Object.hasOwn(value,"states"),records=wrapped ? value.states : value;
  if(!records || typeof records!=="object" || Array.isArray(records))return undefined;
  const entries=Object.entries(records);
  if(entries.length>spec.max_input_items)return undefined;
  const identityPattern=new RegExp(`^(?:${spec.id_pattern})$`),items=[];
  const counts=Object.fromEntries(spec.statuses.map(status=>[status,0]));
  let validCount=0;
  for(const [identity,state] of entries){
    if(identityPattern.exec(identity)?.[0]!==identity || !state || typeof state!=="object" || Array.isArray(state) ||
      typeof state.status!=="string" || !spec.statuses.includes(state.status) ||
      spec.count_fields.some(key=>!Number.isSafeInteger(state[key]) || state[key]<0 ||
        state[key]>evidenceProjection.coverage_evidence.max_count))continue;
    validCount++;counts[state.status]++;
    if(items.length>=spec.max_items)continue;
    const item={canonical_question_id:identity,status:state.status,
      ...Object.fromEntries(spec.count_fields.map(key=>[key,state[key]]))};
    if(typeof state.binding_status==="string" && spec.binding_statuses.includes(state.binding_status))item.binding_status=state.binding_status;
    if(Number.isSafeInteger(state.last_score) && state.last_score>=0 && state.last_score<=spec.max_score)item.last_score=state.last_score;
    items.push(item);
  }
  const result={scope:spec.scope,items,total_count:entries.length,valid_count:validCount,
    invalid_count:entries.length-validCount,returned_count:items.length,truncated:items.length<entries.length,
    status_counts:counts,summary_complete:validCount===entries.length};
  if(wrapped && Number.isSafeInteger(value.user_state_revision) && value.user_state_revision>=0 &&
    value.user_state_revision<=evidenceProjection.coverage_evidence.max_count)result.user_state_revision=value.user_state_revision;
  return result;
}

function evidenceMeta(meta) {
  const projected=Object.fromEntries(evidenceProjection.meta_fields.pi_legacy
    .filter(key=>meta[key]!==undefined).map(key=>[key,meta[key]]));
  const coverage=evidenceProjection.coverage_evidence;
  if(coverage){
    const validated=coverageFields(meta,coverage);
    for(const key of [...coverage.count_fields,...Object.keys(coverage.enum_fields)]){
      if(Object.hasOwn(validated,key))projected[key]=validated[key];
      else delete projected[key];
    }
    const completeness=coverageFields(meta.result_completeness,coverage.result_completeness);
    if(Object.keys(completeness).length)projected.result_completeness=completeness;
    else delete projected.result_completeness;
  }
  if(projected.candidate_workset_progress!==undefined){
    const progress=worksetProgress(projected.candidate_workset_progress);
    if(progress)projected.candidate_workset_progress=progress;
    else delete projected.candidate_workset_progress;
  }
  if(meta.pagination)projected.pagination={...Object.fromEntries(evidenceProjection.pagination_fields
    .filter(key=>!evidenceProjection.pagination_boolean_fields.includes(key) && typeof meta.pagination[key]==="number" || key==="kind" && typeof meta.pagination[key]==="string" ||
      evidenceProjection.pagination_boolean_fields.includes(key) && typeof meta.pagination[key]==="boolean").map(key=>[key,meta.pagination[key]])),
    has_next_page:Boolean(meta.pagination.next_cursor || meta.pagination.has_next_page)};
  if(projected.pagination?.workset===true)delete projected.pagination.total;
  return projected;
}

export async function runQuery(input, {hostUrl, token, signal, streamFn: injectedStream, onEvent} = {}) {
  const {run_id: runId, schema, prompt, context, conversation_id: conversationId} = input;
  if (!runId || !schema || !context || !hostUrl || !token) throw new Error("INVALID_RUN");
  const baseUrl = `${hostUrl.replace(/\/$/, "")}/internal/agent/runs/${encodeURIComponent(runId)}/v1`;
  const model = {id:"query", name:"Query model gateway", provider:"query-host", api:"openai-completions",
    baseUrl, reasoning:false, input:["text"], cost:{input:0,output:0,cacheRead:0,cacheWrite:0},
    contextWindow:64_000,maxTokens:4096,
    compat:{supportsStore:false,supportsDeveloperRole:false,supportsReasoningEffort:false,
      supportsStrictMode:false,supportsUsageInStreaming:true,maxTokensField:"max_tokens"}};
  const models = createModels({authContext:{env:async name => name === "INTERNAL_AGENT_TOKEN" ? token : undefined}});
  models.setProvider(createProvider({id:"query-host",name:"Query model gateway",baseUrl,
    auth:{apiKey:envApiKeyAuth("Internal gateway",["INTERNAL_AGENT_TOKEN"])},models:[model],api:openAICompletionsApi()}));
  const maxModelCalls=input.max_model_calls ?? context.budget?.remaining_planning_calls ?? context.budget?.remaining_model_calls ?? 8;
  if(!Number.isSafeInteger(maxModelCalls) || maxModelCalls<0 || maxModelCalls>32)
    throw new Error("INVALID_MODEL_BUDGET");
  let providerCalls = 0, toolCalls = 0, completed = false, hostProtocolError;
  let agent, policy = context.tool_policy;
  if (input.tool_wire_version !== undefined && input.tool_wire_version !== "query_step_v1")
    throw new Error("INVALID_TOOL_WIRE_VERSION");
  const compactWire = input.tool_wire_version === "query_step_v1";
  const actionTools = Object.fromEntries(Object.entries(toolsByAction)
    .flatMap(([name, actions]) => actions.map(action => [action, name])));
  const domainCalls = new Map();
  const events = [];
  function permittedActions(name) {
    if (policy === undefined) return toolsByAction[name] || [];
    if (!policy || typeof policy.allowed_actions !== "object") throw new Error("INVALID_TOOL_POLICY");
    return (policy.allowed_actions[name] || []).filter(action => toolsByAction[name]?.includes(action));
  }
  function domainParameters(name) {
    const actions = permittedActions(name);
    const parameters = inlineSchema(schema, schema.$defs || {});
    const definition = toolContract.tools[name];
    const allowed=new Set([...toolContract.common_required,...definition.fields,"user_goal"]);
    parameters.properties=Object.fromEntries(Object.entries(parameters.properties).filter(([key])=>allowed.has(key)));
    parameters.required=(parameters.required || []).filter(key=>allowed.has(key));
    parameters.properties.action = {type:"string",enum:actions};
    for (const [key,value] of Object.entries(definition.replace_properties || {}))
      parameters.properties[key] = inlineSchema(value, {});
    for (const [key,value] of Object.entries(definition.merge_properties || {}))
      parameters.properties[key] = {...parameters.properties[key],...inlineSchema(value, {})};
    for (const [key,value] of Object.entries(definition.merge_existing_properties || {}))
      if (parameters.properties[key])
        parameters.properties[key] = {...parameters.properties[key],...inlineSchema(value, {})};
    parameters.required=Array.from(new Set([...(parameters.required || []),...[...definition.required,...(definition.pi_required_extra || [])]
      .filter(key=>Object.hasOwn(parameters.properties,key))]));
    return parameters;
  }
  function domainTool(name) {
    const actions = permittedActions(name), definition = toolContract.tools[name];
    return {name,label:name,description:`Execute ${actions.join("/")} using authoritative host data; fill the declared scope fields.${definition.hint}`,
      parameters:Type.Unsafe(domainParameters(name)), constrainedSampling:{type:"json_schema",strict:"prefer"},
      async execute(_toolCallId,args,toolSignal) {
        if (completed || ++toolCalls > 8) throw new Error("QUERY_TOOL_BUDGET_EXCEEDED");
        const response = await fetch(`${hostUrl.replace(/\/$/, "")}/internal/agent/runs/${encodeURIComponent(runId)}/tools/${name}`, {
          method:"POST",headers:{"Authorization":`Bearer ${token}`,"Content-Type":"application/json"},
          body:JSON.stringify(args),signal:toolSignal});
        const result = await response.json();
        if (!response.ok) throw new Error(result.error?.message || result.error?.code || "QUERY_TOOL_FAILED");
        const completion=result.facts?.meta?.completion;
        if(completion!==undefined && (!completion || typeof completion!=="object" ||
          Array.isArray(completion) || typeof completion.terminal!=="boolean")){
          hostProtocolError="INVALID_HOST_COMPLETION";
          throw new Error(hostProtocolError);
        }
        completed = completion===undefined ? (result.planning?.spec?.final ?? args.final) !== false : completion.terminal;
        const refreshed = result.facts.meta.harness;
        if (refreshed?.tool_policy !== undefined) {
          policy = refreshed.tool_policy;
          agent.state.tools = makeTools();
        }
        // Keep bounded host evidence available to the next reasoning step;
        // complete rows remain in the durable host response.
        return {content:[{type:"text",text:JSON.stringify({answer:result.answer,meta:evidenceMeta(result.facts.meta),
          ...(completion===undefined ? {} : {completion,tool_ordinal:completion.tool_ordinal,
            user_goal:refreshed?.user_goal || result.facts.meta.user_goal}),
          session:refreshed?.session,
          budget:refreshed?.budget,
          current_page_ids:refreshed?.session?.current_page_ids || (result.facts.data || []).map(r=>r.canonical_question_id).filter(Boolean),
          items:Array.isArray(refreshed?.evidence) ? refreshed.evidence : (result.facts.data || []).slice(0,evidenceProjection.limits.tool_rows).map(evidenceRow),
          counts:result.facts.counts || result.facts.meta.counts,
          states:reviewStates(result.facts.states)})}],
          details:{action:args.action},structuredContent:result,terminate:completed};
      }};
  }
  const compactParameters = compactWire ? compactSchema(inlineSchema(schema, schema.$defs || {})) : undefined;
  if (compactParameters) {
    // The invariant schema is not rebuilt from policy: only action.enum changes
    // between turns. Removing a permission cannot weaken another action's rules.
    compactParameters.allOf = [...(compactParameters.allOf || []), ...Object.keys(toolsByAction).map(name => {
      const domain = domainParameters(name), definition = toolContract.tools[name];
      const changed = new Set([...Object.keys(definition.replace_properties || {}),
        ...Object.keys(definition.merge_properties || {}), ...Object.keys(definition.merge_existing_properties || {})]);
      return {if:{properties:{action:{enum:toolsByAction[name]}},required:["action"]},
        then:{required:domain.required,
          ...(changed.size ? {properties:Object.fromEntries([...changed]
            .filter(key => Object.hasOwn(domain.properties,key)).map(key => [key,compactSchema(domain.properties[key])]))} : {})}};
    })];
  }
  const makeTools = () => {
    const names = Object.keys(toolsByAction).filter(name => permittedActions(name).length);
    if (!compactWire) return names.map(domainTool);
    if (!names.length) return [];
    const parameters = structuredClone(compactParameters);
    parameters.properties.action = {type:"string",enum:names.flatMap(name => permittedActions(name))};
    return [{name:"query_step",label:"query_step",description:
      "Execute exactly one currently authorized query action. The host retains authority over permissions, evidence and completion." +
      Object.entries(toolContract.tools).filter(([,tool]) => tool.hint).map(([,tool]) =>
        ` ${tool.actions.join("/")}:${tool.hint}`).join(""),
      parameters:Type.Unsafe(parameters),constrainedSampling:{type:"json_schema",strict:"prefer"},
      execute(toolCallId,args,toolSignal) {
        const name = actionTools[args.action];
        if (!name || !permittedActions(name).includes(args.action)) throw new Error("UNAUTHORIZED_QUERY_ACTION");
        return domainTool(name).execute(toolCallId,args,toolSignal);
      }}];
  };
  const systemPrompt = compactWire ? `${prompt}\nWire query_step_v1: query_step wraps one domain tool; choose its authorized action. Tool-result receipts and host completion remain authoritative.` : prompt;
  agent = new Agent({initialState:{systemPrompt,model,tools:makeTools(),thinkingLevel:"off"},
    sessionId:conversationId,toolExecution:"sequential",
    streamFn:injectedStream || ((m,c,o) => models.streamSimple(m,c,{...o,maxRetries:0,timeout:input.timeout_ms || 60_000})),
    prepareRequest:async () => {if (providerCalls>=maxModelCalls) throw new Error("QUERY_MODEL_BUDGET_EXCEEDED");providerCalls++;},
    prepareNextTurnWithContext:async ({context:current}) => ({context:{...current,tools:makeTools()}}),
    beforeToolCall:async ({assistantMessage,toolCall,args}) => {
      const calls=assistantMessage.content.filter(item => item.type === "toolCall");
      const name = compactWire && toolCall.name === "query_step" ? actionTools[args.action] : toolCall.name;
      if(completed || hostProtocolError || calls.length !== 1 ||
        compactWire && toolCall.name !== "query_step" || !permittedActions(name).includes(args.action))
        return {block:true,reason:"Exactly one currently authorized domain tool call is allowed per step.",terminate:true};
      if (compactWire) domainCalls.set(toolCall.id,name);
    },
    finishTurn:() => ({action:completed || hostProtocolError || providerCalls>=maxModelCalls ? "end" : "continue"}),
  });
  agent.subscribe(async event => {
    if (["turn_start","message_end","tool_execution_start","tool_execution_end","turn_end","agent_end"].includes(event.type)) {
      const safe={type:event.type,toolName:event.toolName,isError:event.isError};
      if(event.toolCallId) safe.toolCallId=event.toolCallId;
      if(compactWire && event.toolName === "query_step") {
        safe.wireToolName=event.toolName;
        safe.toolName=domainCalls.get(event.toolCallId) || actionTools[event.args?.action] || event.toolName;
      }
      if(event.type==="tool_execution_start") safe.args=event.args;
      if(event.type==="tool_execution_end" && event.isError)
        safe.error=String(event.result?.content?.find(item=>item.type==="text")?.text || "QUERY_TOOL_FAILED").slice(0,512);
      if(event.type==="message_end") {
        safe.message={...event.message};
        if(safe.message.usage) {
          safe.message.usage={...safe.message.usage,cost:null,costSource:"UNAVAILABLE"};
        }
      }
      events.push(safe); await onEvent?.(safe);
    }
  });
  const abort=() => agent.abort();
  signal?.addEventListener("abort",abort,{once:true});
  try {
    signal?.throwIfAborted();
    await agent.prompt(JSON.stringify(context));
    await agent.waitForIdle();
    signal?.throwIfAborted();
    if(!completed) throw new Error(hostProtocolError || agent.state.errorMessage ||
      (providerCalls>=maxModelCalls ? "QUERY_MODEL_BUDGET_EXCEEDED" : "QUERY_PLAN_MISSING"));
    return {completed,provider_calls:providerCalls,tool_calls:toolCalls,events};
  } finally {signal?.removeEventListener("abort",abort);}
}
