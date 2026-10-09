(function (root) {
  "use strict";

  const finite = value => typeof value === "number" && Number.isFinite(value);
  function number(value, digits = 0) {
    return finite(value) ? value.toLocaleString("zh-CN", { maximumFractionDigits: digits }) : "—";
  }
  function date(value, compact = false) {
    const parsed = new Date(value);
    if (!value || !Number.isFinite(parsed.getTime())) return "—";
    return new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", ...(compact ? {} : { second: "2-digit" }), hour12: false }).format(parsed);
  }
  function errorMessage(body, status) {
    const known = {
      admin_not_configured: "管理员凭证尚未配置，请先在服务端配置 ADMIN_ACCESS_TOKEN。",
      admin_auth_required: "登录状态已过期，请重新登录。",
      admin_invalid_token: "管理员凭证不正确，请重新输入。",
      admin_invalid_credentials: "管理员凭证不正确，请重新输入。",
      admin_login_rate_limited: "登录尝试过于频繁，请稍后重试。",
      policy_version_conflict: "策略已被其他管理员修改，已重新读取最新配置。请确认后再保存。",
      admin_action_header_required: "操作校验未通过，请刷新页面后重试。",
      admin_origin_rejected: "当前页面来源未通过校验，请从正式管理入口重新打开。"
    };
    const detail = typeof body?.detail === "string" ? body.detail : "";
    const code = String(body?.error?.code || detail).toLowerCase();
    if (known[code]) return known[code];
    if (typeof body?.error?.message === "string") return body.error.message;
    if (status === 401) return "管理员凭证无效或登录已过期，请重新登录。";
    if (status === 409) return "数据已被其他操作更新，请重新读取后再试。";
    if (status === 429) return "操作过于频繁，请稍后重试。";
    if (status === 422) return "参数不符合要求，请检查输入的数值和操作原因。";
    if (status === 503) return "管理服务暂不可用，请稍后重试，或检查管理员凭证配置。";
    return `请求失败（HTTP ${status}），请稍后重试。`;
  }
  function chartModel(series, width = 900, height = 230) {
    const samples = (Array.isArray(series) ? series : []).filter(item =>
      item && finite(item.qps) && item.qps >= 0 && Number.isFinite(new Date(item.timestamp).getTime()))
      .slice().sort((a, b) => new Date(a.timestamp) - new Date(b.timestamp));
    const left = 38, right = width - 10, top = 12, bottom = height - 30;
    const maximum = Math.max(1, ...samples.map(item => item.qps));
    const magnitude = Math.pow(10, Math.floor(Math.log10(maximum)));
    const ceiling = Math.ceil(maximum / magnitude) * magnitude;
    const firstTime = samples.length ? new Date(samples[0].timestamp).getTime() : 0;
    const timeSpan = samples.length > 1 ? new Date(samples.at(-1).timestamp).getTime() - firstTime : 0;
    const points = samples.map(item => ({ ...item,
      x: timeSpan > 0 ? left + (new Date(item.timestamp).getTime() - firstTime) / timeSpan * (right - left) : (left + right) / 2,
      y: bottom - item.qps / ceiling * (bottom - top) }));
    const line = points.map((point, index) => `${index ? "L" : "M"}${point.x.toFixed(2)} ${point.y.toFixed(2)}`).join(" ");
    const area = points.length ? `${line} L${points.at(-1).x.toFixed(2)} ${bottom} L${points[0].x.toFixed(2)} ${bottom} Z` : "";
    return { width, height, left, right, top, bottom, ceiling, points, line, area };
  }
  function policyPayload(form, version) {
    return { expected_version: version, quota_enabled: form.quota_enabled,
      quota_limit: Number(form.quota_limit), quota_period: form.quota_period,
      quota_scope: form.quota_scope, rate_enabled: form.rate_enabled,
      rate_per_minute: Number(form.rate_per_minute) };
  }
  const helpers = { number, date, errorMessage, chartModel, policyPayload };
  if (typeof module !== "undefined" && module.exports) module.exports = helpers;
  root.AdminPanel = helpers;
  if (typeof document === "undefined") return;

  const $ = id => document.getElementById(id);
  const state = { authenticated: false, page: "overview", window: "24h", visitorPage: 1,
    visitorQuery: "", policy: null, policyDirty: false, policySaving: false, action: null,
    actionSaving: false, refreshing: false, refreshQueued: false, loggedOut: false, series: [], visitorRequest: 0,
    overviewRequest: 0 };
  const pages = { overview: "流量概览", visitors: "访客与设备", policy: "使用策略", audit: "操作记录" };
  const policyFields = ["quota_enabled", "quota_limit", "quota_period", "quota_scope", "rate_enabled", "rate_per_minute"];
  const fieldId = key => key.replaceAll("_", "-");
  const setText = (id, value) => { $(id).textContent = String(value); };
  function message(id, value = "", error = false) {
    const element = $(id);
    element.textContent = value;
    element.hidden = !value;
    element.classList.toggle("error", error);
  }
  function element(tag, className, value) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (value !== undefined) node.textContent = String(value);
    return node;
  }
  function svg(tag, attributes, value) {
    const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
    for (const [name, attribute] of Object.entries(attributes || {})) node.setAttribute(name, String(attribute));
    if (value !== undefined) node.textContent = String(value);
    return node;
  }
  async function api(path, options = {}) {
    const method = options.method || "GET";
    const headers = { Accept: "application/json", ...(method !== "GET" ? { "X-Admin-Action": "1" } : {}),
      ...(options.body !== undefined ? { "Content-Type": "application/json" } : {}) };
    let response;
    try {
      response = await fetch(`api/admin/${path}`, { method, headers, credentials: "same-origin",
        cache: "no-store", ...(options.body !== undefined ? { body: JSON.stringify(options.body) } : {}) });
    } catch (cause) {
      throw new Error("无法连接管理服务，请检查网络连接后重试。", { cause });
    }
    let body = null;
    try { body = await response.json(); } catch { /* A proxy can return a non-JSON error. */ }
    if (!response.ok) {
      if (response.status === 401 && state.authenticated) showLogin("登录状态已过期，请重新登录。");
      const failure = new Error(errorMessage(body, response.status));
      failure.status = response.status;
      throw failure;
    }
    if (!body || !Object.hasOwn(body, "data")) throw new Error("服务返回的数据格式不完整，请刷新后重试。");
    return body.data;
  }
  function showLogin(error = "") {
    state.authenticated = false;
    state.policy = null;
    state.policyDirty = false;
    policyFields.forEach(key => { $(fieldId(key)).disabled = true; });
    $("console").hidden = true;
    $("login").hidden = false;
    $("admin-token").value = "";
    setText("session-status", "凭证只用于建立当前浏览器的管理会话。");
    message("login-error", error, true);
    if ($("action-dialog").open) $("action-dialog").close();
  }
  async function showConsole() {
    state.authenticated = true;
    state.loggedOut = false;
    $("admin-token").value = "";
    $("login").hidden = true;
    $("console").hidden = false;
    message("global-message");
    selectPage(location.hash.slice(1) || "overview", false);
    await refresh();
  }
  function selectPage(page, shouldRefresh = true) {
    state.page = Object.hasOwn(pages, page) ? page : "overview";
    document.querySelectorAll(".page").forEach(item => { item.hidden = item.id !== `page-${state.page}`; });
    document.querySelectorAll(".nav-item").forEach(button => {
      const active = button.dataset.page === state.page;
      button.classList.toggle("active", active);
      if (active) button.setAttribute("aria-current", "page"); else button.removeAttribute("aria-current");
    });
    setText("page-name", pages[state.page]);
    document.title = `${pages[state.page]} · 面经研习访问管理`;
    if (location.hash !== `#${state.page}`) history.replaceState(null, "", `${location.pathname}${location.search}#${state.page}`);
    if (shouldRefresh && state.authenticated) refresh();
  }
  function renderChart(series) {
    const model = chartModel(series);
    state.series = model.points;
    const chart = $("chart");
    chart.replaceChildren();
    chart.setAttribute("aria-busy", "false");
    if (!model.points.length) {
      chart.append(element("div", "empty-state", "此时间范围内暂无请求趋势。采集从管理功能启用时开始。"));
    } else {
      const drawing = svg("svg", { viewBox: `0 0 ${model.width} ${model.height}`, role: "img",
        "aria-label": `业务请求 QPS 趋势，${model.points.length} 个时间桶，最高桶平均 ${number(Math.max(...model.points.map(x => x.qps)), 3)} 请求每秒。精确数值见下方数据表。` });
      const defs = svg("defs");
      const gradient = svg("linearGradient", { id: "qps-gradient", x1: 0, y1: 0, x2: 0, y2: 1 });
      gradient.append(svg("stop", { offset: "0%", "stop-color": "#95ba87", "stop-opacity": ".28" }),
        svg("stop", { offset: "100%", "stop-color": "#95ba87", "stop-opacity": ".02" }));
      defs.append(gradient); drawing.append(defs);
      for (let index = 0; index < 4; index++) {
        const y = model.top + (model.bottom - model.top) * index / 3;
        drawing.append(svg("line", { x1: model.left, x2: model.right, y1: y, y2: y, class: "chart-gridline" }),
          svg("text", { x: model.left - 10, y: y + 3, "text-anchor": "end" }, number(model.ceiling * (1 - index / 3), 2)));
      }
      drawing.append(svg("path", { d: model.area, class: "chart-area" }), svg("path", { d: model.line, class: "chart-line" }));
      if (model.points.length === 1) drawing.append(svg("circle", { cx: model.points[0].x, cy: model.points[0].y, r: 3, class: "chart-point" }));
      const labelIndexes = [...new Set([0, Math.floor((model.points.length - 1) / 2), model.points.length - 1])];
      for (const index of labelIndexes) {
        const point = model.points[index];
        drawing.append(svg("text", { x: point.x, y: model.height - 5,
          "text-anchor": index === 0 && model.points.length > 1 ? "start" : index === model.points.length - 1 && model.points.length > 1 ? "end" : "middle" }, date(point.timestamp, true)));
      }
      chart.append(drawing);
    }
    const table = $("chart-table"); table.replaceChildren();
    for (const point of model.points) {
      const row = element("tr");
      for (const value of [date(point.timestamp), number(point.requests), number(point.qps, 4), number(point.errors)]) row.append(element("td", "", value));
      table.append(row);
    }
    if (!model.points.length) { const cell = element("td", "", "暂无数据"); cell.colSpan = 4; const row = element("tr"); row.append(cell); table.append(row); }
  }
  async function loadOverview() {
    const request = ++state.overviewRequest;
    const window = state.window;
    try {
      const data = await api(`overview?window=${encodeURIComponent(window)}`);
      if (request !== state.overviewRequest || window !== state.window || !state.authenticated) return;
      const summary = data.summary || {};
      for (const key of ["visitors", "ips", "page_views", "chat_requests", "blocked_requests"]) setText(`metric-${fieldId(key)}`, number(summary[key]));
      setText("metric-p95-ms", finite(summary.p95_ms) ? `${number(summary.p95_ms)} ms` : "—");
      setText("metric-current-qps", number(summary.current_qps, 3));
      setText("metric-peak-qps", number(summary.peak_qps, 3));
      setText("metric-error-rate", finite(summary.error_rate) ? `${number(summary.error_rate * 100, 2)}%` : "—");
      setText("metric-requests", `${number(summary.requests)} 条业务请求`);
      setText("chart-range", `${date(data.from, true)} — ${date(data.to, true)} · 桶平均 QPS（请求 / 秒）`);
      setText("last-updated", `最近更新 ${date(new Date().toISOString())} · 北京时间`);
      $("metrics").setAttribute("aria-busy", "false");
      renderChart(data.series);
      message("overview-error");
    } catch (error) {
      if (request !== state.overviewRequest || !state.authenticated) return;
      $("metrics").setAttribute("aria-busy", "false");
      $("chart").setAttribute("aria-busy", "false");
      message("overview-error", `${error.message} 已显示的数据可能不是最新数据。`, true);
      setText("last-updated", "本次更新失败");
    }
  }
  function pill(value, className = "") { return element("span", `status-pill ${className}`.trim(), value); }
  function appendRowAction(container, text, className, action) {
    const button = element("button", `row-action ${className}`, text);
    button.type = "button"; button.addEventListener("click", () => openAction(action)); container.append(button);
  }
  function renderVisitors(data) {
    const table = $("visitor-table"); table.replaceChildren();
    const items = Array.isArray(data.items) ? data.items : [];
    for (const item of items) {
      const row = element("tr");
      const profileCell = element("td"), profile = element("div", "device-heading");
      const symbol = element("span", "device-symbol", /mobile|phone|android|ios|手机/i.test(item.device || "") ? "▯" : "▣");
      symbol.setAttribute("aria-hidden", "true");
      const profileText = element("div");
      profileText.append(element("span", "cell-title", `${item.device || "未知设备"} · ${item.browser || "未知浏览器"}`));
      const id = element("span", "cell-note device-id", item.id || "—");
      id.title = item.user_agent || "";
      profileText.append(id);
      if (item.blocked) profileText.append(pill("设备已禁用", "blocked"));
      profile.append(symbol, profileText); profileCell.append(profile); row.append(profileCell);
      const ipCell = element("td", "ip-cell"); ipCell.append(element("span", "ip-address", item.ip || "未知"));
      if (item.ip_blocked) ipCell.append(document.createElement("br"), pill("IP 已禁用", "blocked"));
      row.append(ipCell);
      const counters = element("td", "visitor-counters");
      counters.append(element("span", "", `${number(item.page_views)} PV · ${number(item.requests)} 请求`),
        element("span", "cell-note", `${number(item.chat_requests)} 次对话准入`)); row.append(counters);
      const quota = element("td");
      quota.append(element("span", "quota-value", `${number(item.quota_used)} / ${number(item.quota_limit)}`),
        element("span", "cell-note", state.policy?.quota_enabled ? (state.policy.quota_scope === "IP" ? "IP 共享额度" : "设备额度") : "额度限制已关闭")); row.append(quota);
      const last = element("td"); last.append(element("span", "", date(item.last_seen, true)),
        element("span", "cell-note", `首次 ${date(item.first_seen, true)}`)); row.append(last);
      const actionsCell = element("td"), actions = element("div", "cell-actions");
      appendRowAction(actions, item.blocked ? "启用设备" : "禁用设备", item.blocked ? "" : "danger", { kind: "device", item, blocked: !item.blocked });
      if (item.ip) appendRowAction(actions, item.ip_blocked ? "启用 IP" : "禁用 IP", item.ip_blocked ? "" : "danger", { kind: "ip", item, blocked: !item.ip_blocked });
      appendRowAction(actions, "重置额度", "reset", { kind: "reset", item });
      actionsCell.append(actions); row.append(actionsCell); table.append(row);
    }
    const total = finite(data.total) ? data.total : 0;
    const page = finite(data.page) ? data.page : state.visitorPage;
    const pageSize = finite(data.page_size) && data.page_size > 0 ? data.page_size : 20;
    state.visitorPage = page;
    const pageCount = Math.max(1, Math.ceil(total / pageSize));
    setText("visitor-count", `共 ${number(total)} 个设备 · 每页 ${pageSize} 条`);
    setText("visitor-page", `${page} / ${pageCount}`);
    $("visitors-prev").disabled = page <= 1;
    $("visitors-next").disabled = page >= pageCount;
    $("visitors-empty").hidden = items.length > 0;
    setText("visitors-empty", state.visitorQuery ? "没有匹配的设备。试试其他 IP 或浏览器名称。" : "尚无访客记录。首次真实业务访问后会显示在这里。");
  }
  async function loadVisitors() {
    const request = ++state.visitorRequest;
    const parameters = new URLSearchParams({ q: state.visitorQuery, page: String(state.visitorPage), page_size: "20" });
    $("visitor-table").setAttribute("aria-busy", "true");
    try {
      const data = await api(`visitors?${parameters}`);
      if (request !== state.visitorRequest || !state.authenticated) return;
      renderVisitors(data); message("visitors-error");
    } catch (error) {
      if (request !== state.visitorRequest || !state.authenticated) return;
      message("visitors-error", `${error.message} 下方记录可能不是最新数据。`, true);
      setText("visitor-count", "访客记录更新失败");
    } finally { if (request === state.visitorRequest) $("visitor-table").setAttribute("aria-busy", "false"); }
  }
  function formValues() {
    return Object.fromEntries(policyFields.map(key => [key, key.endsWith("enabled") ? $(fieldId(key)).checked : $(fieldId(key)).value]));
  }
  function updatePolicyDirty() {
    const values = formValues();
    state.policyDirty = !!state.policy && policyFields.some(key => String(values[key]) !== String(state.policy[key]));
    const editable = !!state.policy && !state.policySaving;
    $("policy-save").disabled = !editable || !state.policyDirty;
    $("policy-discard").disabled = !editable || !state.policyDirty;
    setText("policy-dirty", state.policyDirty ? "有尚未保存的修改" : "当前配置已保存");
    setText("policy-saved-at", state.policyDirty ? "点击保存后，配置才会应用到新请求" : "开关与数值需点击保存才会生效");
  }
  function renderPolicy(data) {
    state.policy = data;
    for (const key of policyFields) {
      const control = $(fieldId(key));
      if (key.endsWith("enabled")) control.checked = !!data[key]; else control.value = data[key];
      control.disabled = state.policySaving;
    }
    setText("policy-version", `版本 ${number(data.version)}`);
    $("policy-form").setAttribute("aria-busy", "false");
    state.policyDirty = false; updatePolicyDirty();
    const summary = $("policy-summary"); summary.replaceChildren(
      pill(data.quota_enabled ? `对话额度 · ${number(data.quota_limit)} 次 / ${data.quota_period === "DAY" ? "天" : "累计"}` : "对话额度 · 已关闭", data.quota_enabled ? "on" : ""),
      pill(data.rate_enabled ? `查询限流 · ${number(data.rate_per_minute)} 次 / 分钟` : "查询限流 · 已关闭", data.rate_enabled ? "on" : ""));
  }
  async function loadPolicy(force = false) {
    if ((state.policyDirty || state.policySaving) && !force) return false;
    try {
      const data = await api("policy");
      if (!state.authenticated || (!force && (state.policyDirty || state.policySaving))) return false;
      renderPolicy(data); message("policy-error");
      return true;
    } catch (error) {
      if (!state.authenticated) return;
      message("policy-error", error.message, true);
      $("policy-form").setAttribute("aria-busy", "false");
      if (!state.policy) setText("policy-summary", "策略暂时读取失败");
      return false;
    }
  }
  const actionLabels = {
    DEVICE_BLOCK: "禁用设备", DEVICE_UNBLOCK: "启用设备", IP_BLOCK: "禁用 IP", IP_UNBLOCK: "启用 IP",
    POLICY_UPDATE: "更新使用策略", QUOTA_RESET: "重置对话额度", LOGIN_FAILED: "登录失败",
    LOGIN: "管理员登录", LOGOUT: "管理员退出", LOGIN_SUCCESS: "管理员登录",
    device_block: "禁用设备", device_unblock: "启用设备", ip_block: "禁用 IP", ip_unblock: "启用 IP",
    policy_update: "更新使用策略", quota_reset: "重置对话额度", login_failed: "登录失败"
  };
  function auditDetails(details) {
    if (!details || typeof details !== "object") return "";
    if (typeof details.reason === "string") return `原因：${details.reason}`;
    const fields = { quota_enabled: "对话额度", quota_limit: "提问上限", quota_period: "周期", quota_scope: "额度对象", rate_enabled: "查询限流", rate_per_minute: "每分钟上限", version: "版本", blocked: "禁用" };
    const values = details.after || details.policy || details;
    const displayValue = value => value === true ? "开启" : value === false ? "关闭" : value === "DAY" ? "每天" : value === "LIFETIME" ? "累计" : value === "DEVICE" ? "设备" : value === "IP" ? "IP" : String(value);
    return Object.entries(fields).filter(([key]) => Object.hasOwn(values, key)).map(([key, label]) => `${label}：${displayValue(values[key])}`).join(" · ");
  }
  async function loadAudit() {
    try {
      const data = await api("audit?limit=100");
      if (!state.authenticated) return;
      const list = $("audit-list"); list.replaceChildren();
      const items = Array.isArray(data.items) ? data.items : [];
      for (const item of items) {
        const entry = element("article", "audit-entry"), content = element("div", "audit-content");
        content.append(element("h3", "", actionLabels[item.action] || item.action || "管理操作"),
          element("p", "audit-target", `${item.target || "—"} · ${item.actor || "管理员"}`));
        const detail = auditDetails(item.details);
        if (detail) content.append(element("p", "audit-detail", detail));
        entry.append(element("time", "audit-time", date(item.created_at)), content); list.append(entry);
      }
      if (!items.length) list.append(element("div", "empty-state", "暂无管理操作记录。保存策略或管理设备后，记录会出现在这里。"));
      list.setAttribute("aria-busy", "false"); message("audit-error");
    } catch (error) {
      if (!state.authenticated) return;
      message("audit-error", `${error.message} 操作记录可能不是最新数据。`, true);
      $("audit-list").setAttribute("aria-busy", "false");
    }
  }
  function openAction(action) {
    if (action.kind === "reset" && !state.policy) {
      message("global-message", "当前使用策略尚未读取成功，请刷新后再重置额度。", true);
      return;
    }
    state.action = { ...action, expected_version: state.policy?.version, invalid: false };
    const reset = action.kind === "reset", ip = action.kind === "ip";
    const title = reset ? "重置对话额度" : `${action.blocked ? "禁用" : "启用"}${ip ? " IP" : "设备"}`;
    setText("action-title", title);
    const description = reset ? (state.policy?.quota_scope === "IP" ? "当前额度对象为 IP。重置后，同一网络出口下所有设备共享的用量将归零。" : "将此设备在当前策略周期内的对话用量归零，使它可以继续提问。") :
      ip ? (action.blocked ? "此 IP 可能是公司、校园或家庭的共享网络出口。禁用后，该出口下所有设备的业务访问都会被拒绝。" : "将解除此 IP 的访问限制；已禁用的设备仍需单独启用。") :
        (action.blocked ? "禁用后，此浏览器设备的业务访问将被拒绝。设备更换 IP 后仍保持禁用。" : "将解除此设备的访问限制；若它的 IP 仍被禁用，访问仍会被拒绝。");
    setText("action-description", description);
    const target = ip || (reset && state.policy?.quota_scope === "IP") ? `IP ${action.item.ip || "未知"}` : `设备 ${action.item.id}`;
    setText("action-target", target);
    setText("action-confirm", reset ? "确认重置" : `确认${action.blocked ? "禁用" : "启用"}`);
    $("action-confirm").classList.toggle("danger", reset || action.blocked);
    $("action-confirm").classList.toggle("primary", !reset && !action.blocked);
    $("action-reason").value = "";
    $("action-confirm").disabled = false;
    message("action-error"); $("action-dialog").showModal(); $("action-reason").focus();
  }
  async function saveAction(event) {
    event.preventDefault();
    if (!state.action || state.action.invalid || state.actionSaving) return;
    const reason = $("action-reason").value.trim();
    if (!reason) { message("action-error", "请填写操作原因。", true); return; }
    const action = state.action;
    state.actionSaving = true;
    $("action-confirm").disabled = true; $("action-close").disabled = true; $("action-cancel").disabled = true;
    try {
      if (action.kind === "device") await api(`visitors/${encodeURIComponent(action.item.id)}`, { method: "PATCH", body: { blocked: action.blocked, reason } });
      else if (action.kind === "ip") await api("ips", { method: "PATCH", body: { ip: action.item.ip, blocked: action.blocked, reason } });
      else await api(`visitors/${encodeURIComponent(action.item.id)}/reset-quota`, { method: "POST", body: { reason, expected_version: action.expected_version } });
      $("action-dialog").close();
      message("global-message", `${$("action-title").textContent}已生效。`);
      await Promise.allSettled([loadVisitors(), loadOverview(), loadAudit()]);
    } catch (error) {
      if (!state.authenticated) return;
      if (error.status === 409 && action.kind === "reset") {
        state.action.invalid = true;
        const loaded = await loadPolicy(true);
        if (!loaded) state.policy = null;
        message("action-error", loaded ? "使用策略已改变，本次未重置额度。请关闭窗口并重新发起重置，核对当前额度对象。" : "使用策略已改变，本次未重置额度。最新配置读取失败，请关闭窗口，刷新后重新确认。", true);
      } else message("action-error", error.message, true);
    }
    finally { state.actionSaving = false; $("action-confirm").disabled = !!state.action?.invalid; $("action-close").disabled = false; $("action-cancel").disabled = false; }
  }
  async function savePolicy(event) {
    event.preventDefault();
    if (!state.policy || state.policySaving || !state.policyDirty) return;
    const payload = policyPayload(formValues(), state.policy.version);
    state.policySaving = true; updatePolicyDirty(); message("policy-error");
    policyFields.forEach(key => { $(fieldId(key)).disabled = true; });
    setText("policy-save", "正在保存…");
    try {
      const data = await api("policy", { method: "PATCH", body: payload });
      if (!state.authenticated) return;
      renderPolicy(data); message("global-message", "使用策略已保存，新请求将按此配置执行。");
    } catch (error) {
      if (!state.authenticated) return;
      if (error.status === 409) {
        const reloaded = await loadPolicy(true);
        if (!reloaded) state.policy = null;
        message("policy-error", reloaded ? "策略已被其他管理员修改，已重新读取最新配置。你的修改未保存，请确认后重新调整。" : "策略已被其他管理员修改。最新配置读取失败，已暂停编辑；你的修改未保存，请点击刷新重试。", true);
      } else message("policy-error", error.message, true);
    } finally {
      state.policySaving = false; setText("policy-save", "保存策略 →");
      policyFields.forEach(key => { $(fieldId(key)).disabled = !state.policy; }); updatePolicyDirty();
    }
  }
  async function refresh() {
    if (!state.authenticated) return;
    if (state.refreshing) { state.refreshQueued = true; return; }
    state.refreshing = true; $("refresh-button").disabled = true;
    const tasks = [loadPolicy()];
    if (state.page === "overview") tasks.push(loadOverview());
    if (state.page === "visitors") tasks.push(loadVisitors());
    if (state.page === "audit") tasks.push(loadAudit());
    try { await Promise.allSettled(tasks); }
    finally {
      state.refreshing = false; $("refresh-button").disabled = false;
      if (state.refreshQueued) { state.refreshQueued = false; refresh(); }
    }
  }
  $("login-form").addEventListener("submit", async event => {
    event.preventDefault(); $("login-button").disabled = true; setText("login-button", "正在验证…"); message("login-error");
    try { await api("session", { method: "POST", body: { token: $("admin-token").value } }); await showConsole(); }
    catch (error) { message("login-error", error.status === 401 ? "管理员凭证不正确，请重新输入。" : error.message, true); $("admin-token").select(); }
    finally { $("login-button").disabled = false; setText("login-button", "进入管理面板 →"); }
  });
  $("logout-button").addEventListener("click", async () => {
    $("logout-button").disabled = true;
    try { await api("session", { method: "DELETE" }); state.loggedOut = true; showLogin(); }
    catch (error) { if (state.authenticated) message("global-message", error.message, true); }
    finally { $("logout-button").disabled = false; }
  });
  document.querySelectorAll("[data-page]").forEach(button => button.addEventListener("click", () => selectPage(button.dataset.page)));
  document.querySelectorAll("[data-window]").forEach(button => button.addEventListener("click", () => {
    state.window = button.dataset.window;
    document.querySelectorAll("[data-window]").forEach(item => {
      const active = item.dataset.window === state.window;
      item.classList.toggle("active", active); item.setAttribute("aria-pressed", String(active));
    });
    $("metrics").setAttribute("aria-busy", "true"); $("chart").setAttribute("aria-busy", "true"); loadOverview();
  }));
  $("visitor-search").addEventListener("submit", event => { event.preventDefault(); state.visitorQuery = $("visitor-query").value.trim(); state.visitorPage = 1; loadVisitors(); });
  $("visitors-prev").addEventListener("click", () => { state.visitorPage = Math.max(1, state.visitorPage - 1); loadVisitors(); });
  $("visitors-next").addEventListener("click", () => { state.visitorPage += 1; loadVisitors(); });
  $("policy-form").addEventListener("input", updatePolicyDirty);
  $("policy-form").addEventListener("change", updatePolicyDirty);
  $("policy-form").addEventListener("submit", savePolicy);
  $("policy-discard").addEventListener("click", () => { if (state.policy) renderPolicy(state.policy); message("policy-error"); });
  $("action-form").addEventListener("submit", saveAction);
  ["action-close", "action-cancel"].forEach(id => $(id).addEventListener("click", () => { if (!state.actionSaving) $("action-dialog").close(); }));
  $("action-dialog").addEventListener("cancel", event => { if (state.actionSaving) event.preventDefault(); });
  $("refresh-button").addEventListener("click", refresh);
  window.addEventListener("hashchange", () => { if (state.authenticated) selectPage(location.hash.slice(1)); });
  document.addEventListener("visibilitychange", () => {
    setText("refresh-status", document.hidden ? "页面在后台，自动刷新已暂停" : "每 10 秒自动刷新");
    if (!document.hidden && state.authenticated) refresh();
  });
  setInterval(() => { if (!document.hidden && state.authenticated) refresh(); }, 10000);
  policyFields.forEach(key => { $(fieldId(key)).disabled = true; });
  (async () => {
    try { await api("session"); await showConsole(); }
    catch (error) { showLogin(error.status === 401 ? "" : error.message); }
  })();
})(typeof globalThis !== "undefined" ? globalThis : window);
