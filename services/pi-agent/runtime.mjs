import {Agent} from "@earendil-works/pi-agent-core";
import {createModels, createProvider, envApiKeyAuth, Type} from "@earendil-works/pi-ai";
import {openAICompletionsApi} from "@earendil-works/pi-ai/api/openai-completions.lazy";

const toolsByAction = {
  list_questions: ["LIST", "NEXT", "CLARIFY"], search_questions: ["SEARCH"],
  get_question_stats: ["STATS"], get_question_details: ["DETAILS"],
  get_review_state: ["REVIEW_STATE"], record_review: ["RECORD_REVIEW"],
};

function inlineSchema(value, definitions) {
  if (Array.isArray(value)) return value.map(v => inlineSchema(v, definitions));
  if (value === null || typeof value !== "object") return value;
  if (value.$ref?.startsWith("#/$defs/")) return inlineSchema(definitions[value.$ref.slice(8)], definitions);
  return Object.fromEntries(Object.entries(value).filter(([k]) => k !== "$defs")
    .map(([k,v]) => [k, inlineSchema(v, definitions)]));
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
  const events = [];
  const tools = Object.entries(toolsByAction).map(([name, actions]) => {
    const parameters = inlineSchema(schema, schema.$defs || {});
    parameters.properties.action = {type:"string",enum:actions};
    const extraRequired={get_question_stats:["group_by"],search_questions:["search_query","pipeline"],
      get_question_details:["question_ids"],get_review_state:["question_ids"],record_review:["review_items"]};
    parameters.required=Array.from(new Set([...(parameters.required || []),...(extraRequired[name] || [])
      .filter(key=>Object.hasOwn(parameters.properties,key))]));
    return {name,label:name,description:`Execute ${actions.join("/")} using authoritative host data; provide a complete QuerySpec.`,
      parameters:Type.Unsafe(parameters), constrainedSampling:{type:"json_schema",strict:"prefer"},
      async execute(_toolCallId,args,toolSignal) {
        if (completed || ++toolCalls > 8) throw new Error("QUERY_TOOL_BUDGET_EXCEEDED");
        const response = await fetch(`${hostUrl.replace(/\/$/, "")}/internal/agent/runs/${encodeURIComponent(runId)}/tools/${name}`, {
          method:"POST",headers:{"Authorization":`Bearer ${token}`,"Content-Type":"application/json"},
          body:JSON.stringify(args),signal:toolSignal});
        const result = await response.json();
        if (!response.ok) throw new Error(result.error?.message || result.error?.code || "QUERY_TOOL_FAILED");
        completed = args.final !== false;
        // Complete rows live in the host/PG and typed UI response, without another generation pass.
        return {content:[{type:"text",text:JSON.stringify({answer:result.answer,meta:result.facts.meta,
          current_page_ids:(result.facts.data || []).map(r=>r.canonical_question_id).filter(Boolean),
          items:(result.facts.data || []).slice(0,20),states:result.facts.states})}],
          details:{action:args.action},structuredContent:result,terminate:completed};
      }};
  });
  const agent = new Agent({initialState:{systemPrompt:prompt,model,tools,thinkingLevel:"off"},
    sessionId:conversationId,toolExecution:"sequential",
    streamFn:injectedStream || ((m,c,o) => models.streamSimple(m,c,{...o,maxRetries:0,timeout:input.timeout_ms || 60_000})),
    prepareRequest:async () => {if (++providerCalls > 3) throw new Error("QUERY_MODEL_BUDGET_EXCEEDED");},
    beforeToolCall:async ({assistantMessage,toolCall}) => {
      const calls=assistantMessage.content.filter(item => item.type === "toolCall");
      if(completed || calls.length !== 1 || !Object.hasOwn(toolsByAction,toolCall.name))
        return {block:true,reason:"Exactly one final domain tool call is allowed per turn.",terminate:true};
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
