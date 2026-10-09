import {Agent} from "@earendil-works/pi-agent-core";
import {createModels, createProvider, envApiKeyAuth, Type} from "@earendil-works/pi-ai";
import {openAICompletionsApi} from "@earendil-works/pi-ai/api/openai-completions.lazy";

const toolsByAction = {
  list_questions: ["LIST", "NEXT", "CLARIFY"], search_questions: ["SEARCH"],
  get_question_stats: ["STATS"], get_question_details: ["DETAILS"],
  get_question_count: ["COUNT"], answer_question: ["ANSWER"],
  get_review_state: ["REVIEW_STATE"], record_review: ["RECORD_REVIEW"],
};

function inlineSchema(value, definitions) {
  if (Array.isArray(value)) return value.map(v => inlineSchema(v, definitions));
  if (value === null || typeof value !== "object") return value;
  if (value.$ref?.startsWith("#/$defs/")) return inlineSchema(definitions[value.$ref.slice(8)], definitions);
  return Object.fromEntries(Object.entries(value).filter(([k]) => k !== "$defs")
    .map(([k,v]) => [k, inlineSchema(v, definitions)]));
}

function evidenceRow(row) {
  const projected=Object.fromEntries(["canonical_question_id","key","occurrence_count","interview_count",
    "source_document_count","company_count"].filter(key=>row[key]!==undefined).map(key=>[key,row[key]]));
  if(typeof row.canonical_text==="string")projected.canonical_text=row.canonical_text.slice(0,400);
  if(Array.isArray(row.sources))projected.sources=row.sources.slice(0,2).map(source=>
    Object.fromEntries(["revision_id","start_line","end_line","quote"].filter(key=>source[key]!==undefined)
      .map(key=>[key,key==="quote" ? String(source[key]).slice(0,160) : source[key]])));
  return projected;
}

function evidenceMeta(meta) {
  const projected=Object.fromEntries(["route","corpus_revision","task_annotation_revision","user_state_revision",
    "applied_filters","group_by","topic_level","sort","counts","sample_counts","total_groups","count_scope",
    "review_statuses","review_order","relevance_status","returned_count"]
    .filter(key=>meta[key]!==undefined).map(key=>[key,meta[key]]));
  if(meta.pagination)projected.pagination={...Object.fromEntries(["total","result_total","returned","offset","page_size","top_n"]
    .filter(key=>typeof meta.pagination[key]==="number").map(key=>[key,meta.pagination[key]])),
    has_next_page:Boolean(meta.pagination.next_cursor || meta.pagination.has_next_page)};
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
  let providerCalls = 0, toolCalls = 0, completed = false;
  let agent, policy = context.tool_policy;
  const events = [];
  function permittedActions(name) {
    if (policy === undefined) return toolsByAction[name] || [];
    if (!policy || typeof policy.allowed_actions !== "object") throw new Error("INVALID_TOOL_POLICY");
    return (policy.allowed_actions[name] || []).filter(action => toolsByAction[name]?.includes(action));
  }
  const makeTools = () => Object.keys(toolsByAction).filter(name => permittedActions(name).length).map(name => {
    const actions = permittedActions(name);
    const parameters = inlineSchema(schema, schema.$defs || {});
    const fields={list_questions:["final","clarification","clarification_options","review_statuses","review_order"],
      search_questions:["final","search_query","relevance_query","lexical_facets","preferred_question_type","pipeline"],get_question_stats:["final","group_by","topic_level","review_statuses","review_order"],
      get_question_details:["final","question_ids","scope"],get_review_state:["final","question_ids","scope"],
      record_review:["final","review_items","scope"],get_question_count:["final","review_statuses"],
      answer_question:["final","answer_text","answer_kind","answer_basis","question_ids"]};
    const allowed=new Set(["action","filters","sort","top_n","page_size",...fields[name]]);
    parameters.properties=Object.fromEntries(Object.entries(parameters.properties).filter(([key])=>allowed.has(key)));
    parameters.required=(parameters.required || []).filter(key=>allowed.has(key));
    parameters.properties.action = {type:"string",enum:actions};
    if (name === "get_question_count") parameters.properties.top_n = {type:"null"};
    if (name === "search_questions") {
      parameters.properties.top_n = {type:"null"};
      parameters.properties.page_size = {...parameters.properties.page_size,type:"integer",minimum:1,maximum:50};
    }
    if (name === "answer_question") {
      parameters.properties.final = {type:"boolean",const:true};
      if (parameters.properties.question_ids)
        parameters.properties.question_ids = {...parameters.properties.question_ids,maxItems:10};
    }
    const extraRequired={get_question_stats:["group_by"],search_questions:["search_query","pipeline"],
      get_question_details:["question_ids"],get_review_state:["question_ids"],record_review:["review_items"],
      answer_question:["answer_text","answer_kind","answer_basis","final"]};
    parameters.required=Array.from(new Set([...(parameters.required || []),...(extraRequired[name] || [])
      .filter(key=>Object.hasOwn(parameters.properties,key))]));
    const countHint = {list_questions:" Use LIST when the user requests a question list. If the current user goal is COUNT and the message only corrects or restates its subject, continue COUNT even if the previous count already used that subject; do not infer a new list request. For LIST without an explicit requested N, set top_n=null and use page_size for one page. Never infer Top 20 from a default page_size of 20; keep the complete scope pageable.",
      search_questions:" SEARCH requires top_n=null; use page_size (1–50) for the requested result count.",
      get_question_stats:" Group authoritative counts by topic, company or round. Use topic_level=L2 for a breakdown within one L1 topic such as Redis; use L1 for broad topic distribution.",
      get_question_count:" Count the complete current discussion scope within filters, not a result page. A bare total question inherits the current topic; only an explicit whole-corpus request clears it. When the current goal is COUNT, subject corrections or restatements remain COUNT even after an already-correct count. Use final=false when the count is evidence for a subsequent answer.",
      answer_question:" Finish with a direct explanation, comparison, solution, study plan or conversation response. Set final=true and identify the knowledge basis. Cite only question_ids returned by host tools; corpus sources establish interview questions, not the correctness of a generated answer."}[name] || "";
    return {name,label:name,description:`Execute ${actions.join("/")} using authoritative host data; fill the declared scope fields.${countHint}`,
      parameters:Type.Unsafe(parameters), constrainedSampling:{type:"json_schema",strict:"prefer"},
      async execute(_toolCallId,args,toolSignal) {
        if (completed || ++toolCalls > 8) throw new Error("QUERY_TOOL_BUDGET_EXCEEDED");
        const response = await fetch(`${hostUrl.replace(/\/$/, "")}/internal/agent/runs/${encodeURIComponent(runId)}/tools/${name}`, {
          method:"POST",headers:{"Authorization":`Bearer ${token}`,"Content-Type":"application/json"},
          body:JSON.stringify(args),signal:toolSignal});
        const result = await response.json();
        if (!response.ok) throw new Error(result.error?.message || result.error?.code || "QUERY_TOOL_FAILED");
        completed = (result.planning?.spec?.final ?? args.final) !== false;
        const refreshed = result.facts.meta.harness;
        if (refreshed?.tool_policy !== undefined) {
          policy = refreshed.tool_policy;
          agent.state.tools = makeTools();
        }
        // Keep bounded host evidence available to the next reasoning step;
        // complete rows remain in the durable host response.
        return {content:[{type:"text",text:JSON.stringify({answer:result.answer,meta:evidenceMeta(result.facts.meta),
          session:refreshed?.session,
          current_page_ids:refreshed?.session?.current_page_ids || (result.facts.data || []).map(r=>r.canonical_question_id).filter(Boolean),
          items:Array.isArray(refreshed?.evidence) ? refreshed.evidence : (result.facts.data || []).slice(0,5).map(evidenceRow),
          counts:result.facts.counts || result.facts.meta.counts,
          states:result.facts.states})}],
          details:{action:args.action},structuredContent:result,terminate:completed};
      }};
  });
  agent = new Agent({initialState:{systemPrompt:prompt,model,tools:makeTools(),thinkingLevel:"off"},
    sessionId:conversationId,toolExecution:"sequential",
    streamFn:injectedStream || ((m,c,o) => models.streamSimple(m,c,{...o,maxRetries:0,timeout:input.timeout_ms || 60_000})),
    prepareRequest:async () => {if (++providerCalls > 3) throw new Error("QUERY_MODEL_BUDGET_EXCEEDED");},
    prepareNextTurnWithContext:async ({context:current}) => ({context:{...current,tools:makeTools()}}),
    beforeToolCall:async ({assistantMessage,toolCall,args}) => {
      const calls=assistantMessage.content.filter(item => item.type === "toolCall");
      if(completed || calls.length !== 1 || !permittedActions(toolCall.name).includes(args.action))
        return {block:true,reason:"Exactly one currently authorized domain tool call is allowed per step.",terminate:true};
    },
    finishTurn:() => ({action:completed || providerCalls >= 3 ? "end" : "continue"}),
  });
  agent.subscribe(async event => {
    if (["turn_start","message_end","tool_execution_start","tool_execution_end","turn_end","agent_end"].includes(event.type)) {
      const safe={type:event.type,toolName:event.toolName,isError:event.isError};
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
    if(!completed) throw new Error(agent.state.errorMessage || "QUERY_PLAN_MISSING");
    return {completed,provider_calls:providerCalls,tool_calls:toolCalls,events};
  } finally {signal?.removeEventListener("abort",abort);}
}
