(function (root) {
  "use strict";
  const labels = { RUNNING: "运行中", QUEUED: "排队中", SUCCEEDED: "成功", COMPLETED: "成功",
    FAILED: "失败", INTERRUPTED: "已中断", CANCELLED: "已取消", PARTIAL_FAILURE: "部分失败",
    NEEDS_REVIEW: "待核验", STARTED: "执行中", UNKNOWN: "结局未知", PENDING: "待执行" };
  function duration(value) {
    if (typeof value !== "number" || !Number.isFinite(value)) return "未知";
    if (value < 1000) return `${Math.round(value)} ms`;
    if (value < 60000) return `${(value / 1000).toFixed(1)} 秒`;
    return `${Math.floor(value / 60000)} 分 ${Math.round(value % 60000 / 1000)} 秒`;
  }
  const percent = value => typeof value === "number" && Number.isFinite(value) ? `${(value * 100).toFixed(1)}%` : "—";
  function eventLabel(event) {
    const data = event.data || {};
    const stages = { planning: "规划任务", model: "请求模型", model_wait: "等待模型",
      queue: "等待调用配额", tool: "执行工具", retrieval: "召回候选", rerank: "核验候选相关性",
      embedding: "生成查询向量", lexical: "关键词检索", candidate_search: "检索候选",
      waiting_for_model: "等待模型调用", eligibility: "核对筛选范围", retrieving: "召回候选题目",
      reranking: "模型核验候选相关性", assembling: "整理检索结果",
      cancel_requested: "收到取消请求", model_started: "模型调用开始", model_completed: "模型调用完成" };
    if (event.type === "stage") return `${stages[data.stage] || data.stage || "执行阶段"}${data.tool ? ` · ${data.tool}` : ""}`;
    if (event.type === "tool_error" && data.status === "CANCELLED") return "工具调用已取消";
    if (event.type === "tool_error" && data.status === "INTERRUPTED") return "工具调用已中断";
    if (event.type === "pi_event" && data.type === "message_end" && data.message?.role === "user") return "向 Agent 传入任务上下文";
    if (event.type === "pi_event" && data.type === "message_end" && ["tool", "toolResult"].includes(data.message?.role)) return "Agent 接收工具结果";
    if (event.type === "pi_event") return ({ turn_start: "Agent 开始一轮规划", turn_end: "Agent 完成一轮规划",
      agent_end: "Agent 执行结束", message_end: "Agent 返回消息", tool_execution_start: "Agent 发起工具调用",
      tool_execution_end: data.isError ? "Agent 工具调用失败" : "Agent 工具调用结束" })[data.type] || data.type;
    return ({ accepted: "任务已接收", tool_result: "工具结果已保存", tool_error: "工具调用失败",
      completed: "任务完成", failed: "任务失败", interrupted: "任务中断", model_result: "模型返回规划结果", model_request: "向模型提交请求",
      model_call: "模型调用", text_delta: "模型输出增量", answer_delta: "回答输出增量",
      clarification_delta: "澄清问题输出增量", EXTRACT: "抽取面经", DEDUP: "语义去重",
      PUBLISH: "发布题库", INDEX: "同步索引", ANNOTATE: "标注任务" })[event.type] || event.type;
  }
  function route(hash) {
    const parts = hash.replace(/^#/, "").split("/");
    return parts.length === 3 && parts[0] === "agent" && ["QUERY", "INGEST"].includes(parts[1]) && /^[\w-]{1,128}$/.test(parts[2])
      ? { kind: parts[1], id: parts[2] } : null;
  }
  const helpers = { duration, percent, eventLabel, route };
  if (typeof module !== "undefined" && module.exports) module.exports = helpers;
  if (typeof document === "undefined") return;

  const $ = id => document.getElementById(id);
  const state = { api: null, page: 1, request: 0, detailRequest: 0, detail: null, events: new Map(), tab: "timeline" };
  function node(tag, className, value) {
    const item = document.createElement(tag);
    if (className) item.className = className;
    if (value !== undefined) item.textContent = String(value);
    return item;
  }
  function date(value) { return root.AdminPanel.date(value); }
  function count(value) { return typeof value === "number" ? root.AdminPanel.number(value) : "未知"; }
  function status(value) {
    const theme = ["SUCCEEDED", "COMPLETED"].includes(value) ? "success" :
      ["FAILED", "INTERRUPTED", "PARTIAL_FAILURE"].includes(value) ? "failure" :
      ["RUNNING", "STARTED", "QUEUED"].includes(value) ? "running" : "";
    return node("span", `agent-status ${theme}`, labels[value] || value || "未知");
  }
  function error(value = "") { $("agent-error").textContent = value; $("agent-error").hidden = !value; }
  function evidence(label, value, key) {
    const details = node("details"); details.dataset.evidence = key;
    details.append(node("summary", "", label), node("pre", "", JSON.stringify(value, null, 2) ?? "暂无记录"));
    return details;
  }
  function table(headers, rows) {
    if (!rows.length) return node("div", "empty-state", "当前范围暂无记录。");
    const result = node("table"), head = node("thead"), line = node("tr"), body = node("tbody");
    for (const title of headers) { const th = node("th", "", title); th.scope = "col"; line.append(th); }
    head.append(line);
    for (const values of rows) { const tr = node("tr"); for (const value of values) {
      const cell = node("td"); cell.append(value instanceof Node ? value : node("span", "", value)); tr.append(cell);
    } body.append(tr); }
    result.append(head, body); return result;
  }
  function renderSummary(data) {
    const metric = (title, value, caption) => {
      const card = node("article", "metric-card"); card.append(node("div", "", title), node("strong", "", value), node("small", "", caption)); return card;
    };
    $("agent-metrics").replaceChildren(
      metric("任务运行", count(data.total), `${count(data.running)} 运行中 · ${count(data.cancelled)} 已取消`),
      metric("任务失败率", percent(data.failure_rate), `${count(data.failed)} 失败 / ${count(data.finished)} 已完成`),
      metric("P95 完整耗时", duration(data.p95_ms), `${count(data.duration_samples)} 条有完整时间的任务`),
      metric("工具失败率", percent(data.tool_failure_rate), `${count(data.tool_calls)} 次工具调用`),
      metric("模型失败率", percent(data.model_failure_rate), `${count(data.model_calls)} 次模型调用`),
      metric("成功任务", count(data.statuses.SUCCEEDED || 0), `${count(data.statuses.QUEUED || 0)} 排队 · ${count(data.statuses.NEEDS_REVIEW || 0)} 待核验`));
    $("agent-tools-summary").replaceChildren(table(["工具", "调用", "失败", "失败率", "未定 / 取消"],
      data.tools.map(item => [item.name, count(item.total), count(item.failed), percent(item.failure_rate), `${item.unknown} / ${item.cancelled}`])));
    $("agent-models-summary").replaceChildren(table(["模型 / 用途", "调用", "失败率", "平均耗时", "输入 / 输出 Token"],
      data.models.map(item => { const title = node("div"); title.append(node("strong", "cell-title", item.model), node("small", "cell-note", item.operation));
        return [title, count(item.total), percent(item.failure_rate), duration(item.average_ms), `${count(item.input_tokens)} / ${count(item.output_tokens)}`]; })));
    $("agent-updated").textContent = `更新于 ${date(data.generated_at)}`;
  }
  function renderRuns(data) {
    const rows = data.items.map(item => {
      const title = node("div"), link = node("button", "agent-run-link", item.message); link.type = "button";
      link.addEventListener("click", () => { location.hash = `agent/${item.kind}/${item.id}`; });
      title.append(link, node("small", "cell-note device-id", item.id));
      if (item.error_code) title.append(node("small", "cell-note agent-detail-error", item.error_code));
      const kind = node("div"); kind.append(node("span", "cell-title", item.kind === "QUERY" ? "查询 Agent" : "导入与索引"),
        node("small", "cell-note device-id", item.user_id || "后台任务"));
      return [title, kind, status(item.status), `${duration(item.duration_ms)}${item.status === "RUNNING" ? "（进行中）" : ""}`,
        `${item.tool_calls} / ${item.model_calls}`, date(item.created_at)];
    });
    if (rows.length) {
      const rendered = table([], rows); $("agent-runs").replaceChildren(...rendered.querySelector("tbody").children);
    } else { const tr = node("tr"), td = node("td", "empty-state", "当前筛选下暂无运行任务。可以扩大时间范围或切换状态。"); td.colSpan = 6; tr.append(td); $("agent-runs").replaceChildren(tr); }
    $("agent-run-count").textContent = `共 ${count(data.total)} 条任务`;
    $("agent-page").textContent = data.page;
    $("agent-prev").disabled = data.page <= 1;
    $("agent-next").disabled = data.page * data.page_size >= data.total;
  }
  function renderTimeline() {
    const items = [...state.events.values()].sort((a, b) => a.sequence - b.sequence);
    const output = [];
    for (const event of items) {
      const streamed = ["text_delta", "answer_delta", "clarification_delta"].includes(event.type);
      const previous = output.at(-1);
      if (streamed && previous?.streamed) { previous.count++; previous.event = event; }
      else output.push({ event, streamed, count: 1 });
    }
    const entries = output.map(({ event, streamed, count: pieces }) => {
      const failure = ["failed", "interrupted"].includes(event.type) ||
        (event.type === "tool_error" && !["CANCELLED", "INTERRUPTED"].includes(event.data?.status)) || event.data?.isError;
      const entry = node("div", `agent-timeline-entry ${failure ? "failure" : ""}`), time = node("div", "agent-event-time");
      time.append(node("div", "", `#${event.sequence}`), node("div", "", duration(event.offset_ms)));
      const body = node("div", "agent-event-body");
      body.append(node("h3", "", streamed ? `模型输出增量 · ${pieces} 条记录` : eventLabel(event)));
      const data = event.data || {}, summary = data.error_code || data.error || data.code ||
        data.result?.answer?.slice(0, 500) || data.toolName || data.tool || data.message?.stopReason || data.stage;
      if (summary) body.append(node("p", "", summary));
      if (typeof data.count === "number") body.append(node("p", "", `${data.count} 道候选题目`));
      if (typeof data.attempt === "number") body.append(node("p", "", `第 ${data.attempt} 次模型调用`));
      body.append(evidence("查看记录", data, `event-${event.sequence}`)); entry.append(time, body); return entry;
    });
    $("agent-timeline").replaceChildren(...(entries.length ? entries : [node("div", "empty-state", "此任务暂无持久化步骤记录。") ]));
    $("agent-more-events").hidden = !state.detail.has_more;
    $("agent-more-events").textContent = `加载更多步骤（已读取 ${state.events.size} / ${state.detail.last_sequence}）`;
  }
  function renderDetail(data) {
    const expanded = new Set([...document.querySelectorAll("[data-evidence][open]")].map(item => item.dataset.evidence));
    const head = $("agent-detail-head"), meta = node("div", "agent-detail-meta");
    meta.append(node("span", "", `开始：${date(data.created_at)}`), node("span", "", `耗时：${duration(data.duration_ms)}`),
      node("span", "", `工具 ${data.tools.length} 次 · 模型 ${data.models.length} 次`));
    if (data.attempts) meta.append(node("span", "", `执行尝试 ${data.attempts} 次`));
    meta.append(node("code", "", `运行 ID：${data.id}`));
    if (data.user_id) meta.append(node("code", "", `账号：${data.user_id}`));
    if (data.conversation_id) meta.append(node("code", "", `会话：${data.conversation_id}`));
    head.replaceChildren(status(data.status), node("h2", "", data.message), meta);
    if (data.error_code) head.append(node("p", "agent-detail-error", `失败原因：${data.error_code}`));
    const tools = data.tools.map(item => {
      const entry = node("div", "agent-tool-entry"), heading = node("div", "agent-tool-heading");
      heading.append(node("strong", "", `${item.ordinal ? `#${item.ordinal} ` : ""}${item.name}`), status(item.status), node("span", "small muted", duration(item.duration_ms)));
      entry.append(heading, evidence("工具入参", item.arguments, `args-${item.id}`), evidence("工具返回 / 错误", item.result, `result-${item.id}`)); return entry;
    });
    $("agent-tab-tools").replaceChildren(...(tools.length ? tools : [node("div", "empty-state", data.kind === "INGEST" ? "导入流程的步骤记录见执行时间线。" : "此任务未记录工具调用。") ]));
    $("agent-tab-models").replaceChildren(table(["模型 / 用途", "状态", "完整耗时", "排队 / 首 Token", "输入 / 输出 Token", "重试"],
      data.models.map(item => { const title = node("div"); title.append(node("strong", "cell-title", item.model),
        node("small", "cell-note", `${item.operation} · ${item.prompt_version}`));
        if (item.error_code) title.append(node("small", "cell-note agent-detail-error", item.error_code));
        return [title, status(item.status), duration(item.duration_ms), `${duration(item.queue_ms)} / ${duration(item.ttft_ms)}`,
          `${count(item.input_tokens)} / ${count(item.output_tokens)}`, count(item.retry_count)]; })));
    if (data.models_truncated) $("agent-tab-models").append(node("p", "small muted", "当前展示前 500 条模型调用。"));
    $("agent-tab-result").replaceChildren(evidence("查看任务结果", data.result, "final-result"));
    if (data.config) $("agent-tab-result").append(evidence("运行配置与失败记录", data.config, "pipeline-config"));
    renderTimeline();
    for (const item of document.querySelectorAll("[data-evidence]")) if (expanded.has(item.dataset.evidence)) item.open = true;
    $("agent-detail-refresh").textContent = ["RUNNING", "QUEUED"].includes(data.status) ? "运行中 · 每 10 秒更新" : "运行记录已保存";
  }
  async function loadDetail(selected, more = false, force = false) {
    const same = state.detail?.id === selected.id && state.detail?.kind === selected.kind;
    if (same && !more && !force && !["RUNNING", "QUEUED"].includes(state.detail.status)) return;
    const current = ++state.detailRequest;
    if (!same) { state.detail = null; state.events.clear(); $("agent-detail-head").replaceChildren(node("div", "empty-state", "正在读取任务…"));
      for (const key of ["timeline", "tools", "models", "result"]) $(key === "timeline" ? "agent-timeline" : `agent-tab-${key}`).replaceChildren(); }
    const after = same ? state.detail.next_after : 0;
    try {
      const data = await state.api(`agent/runs/${selected.kind}/${encodeURIComponent(selected.id)}?after=${after}`);
      if (current !== state.detailRequest || route(location.hash)?.id !== selected.id) return;
      state.detail = data;
      for (const event of data.events) state.events.set(event.sequence, event);
      renderDetail(data); error();
    } catch (failure) { if (current === state.detailRequest) error(failure.message); }
  }
  async function load(api, forceDetail = false) {
    state.api = api;
    const selected = route(location.hash);
    $("agent-list-view").hidden = !!selected; $("agent-detail-view").hidden = !selected;
    if (selected) { await loadDetail(selected, false, forceDetail); return; }
    state.detailRequest++;
    const current = ++state.request;
    const params = new URLSearchParams({ window: $("agent-window").value, kind: $("agent-kind").value });
    const listParams = new URLSearchParams(params);
    listParams.set("status", $("agent-status").value); listParams.set("q", $("agent-query").value.trim()); listParams.set("page", state.page);
    const responses = await Promise.allSettled([api(`agent/summary?${params}`), api(`agent/runs?${listParams}`)]);
    if (current !== state.request || route(location.hash)) return;
    if (responses[0].status === "fulfilled") renderSummary(responses[0].value);
    if (responses[1].status === "fulfilled") renderRuns(responses[1].value);
    error(responses.filter(item => item.status === "rejected").map(item => item.reason.message).join("；"));
  }
  function reset() {
    state.request++; state.detailRequest++; state.detail = null; state.events.clear(); state.api = null;
    for (const id of ["agent-metrics", "agent-runs", "agent-tools-summary", "agent-models-summary", "agent-detail-head", "agent-timeline", "agent-tab-tools", "agent-tab-models", "agent-tab-result"]) $(id).replaceChildren();
  }
  $("agent-filters").addEventListener("submit", event => { event.preventDefault(); state.page = 1; if (state.api) load(state.api); });
  for (const id of ["agent-window", "agent-kind", "agent-status"]) $(id).addEventListener("change", () => { state.page = 1; if (state.api) load(state.api); });
  for (const [id, change] of [["agent-prev", -1], ["agent-next", 1]]) $(id).addEventListener("click", () => { state.page = Math.max(1, state.page + change); if (state.api) load(state.api); });
  for (const button of document.querySelectorAll("[data-agent-tab]")) button.addEventListener("click", () => {
    state.tab = button.dataset.agentTab;
    for (const tab of document.querySelectorAll("[data-agent-tab]")) { const active = tab.dataset.agentTab === state.tab;
      tab.setAttribute("aria-selected", String(active)); $("agent-tab-" + tab.dataset.agentTab).hidden = !active; }
  });
  $("agent-more-events").addEventListener("click", async () => { const selected = route(location.hash);
    if (!selected || !state.api) return; $("agent-more-events").disabled = true;
    try { await loadDetail(selected, true); } finally { $("agent-more-events").disabled = false; }
  });
  root.AgentObservation = { load, reset };
})(typeof globalThis !== "undefined" ? globalThis : window);
