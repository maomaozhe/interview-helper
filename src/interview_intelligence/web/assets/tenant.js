(function (root) {
  "use strict";

  function errorMessage(payload, status) {
    const messages = {
      TENANT_AUTH_REQUIRED: "登录状态已过期，请重新登录后继续。",
      TENANT_INVALID_CREDENTIALS: "用户名或密码不正确，请重新输入。",
      TENANT_LOGIN_INVALID: "用户名或密码不正确，请重新输入。",
      TENANT_USERNAME_TAKEN: "这个用户名已被使用，请换一个用户名。",
      TENANT_LOGIN_RATE_LIMITED: "登录尝试过于频繁，请稍后重试。",
      TENANT_REGISTRATION_RATE_LIMITED: "注册尝试过于频繁，请稍后重试。",
      TENANT_MODEL_VERSION_CONFLICT: "模型配置已发生变化，请重新读取后再保存。",
      TENANT_MODEL_CONFIGURATION_INCOMPLETE: "请填写完整的接口地址、模型名称和 API Key。",
      TENANT_MODEL_ENDPOINT_INVALID: "接口地址无效，请填写兼容 OpenAI 的 HTTP 或 HTTPS 地址。",
      PROVIDER_VERSION_CONFLICT: "模型配置已发生变化，请重新读取后再保存。",
      PROVIDER_API_KEY_REQUIRED: "首次配置个人模型时需要填写自己的 API Key。",
      PROVIDER_MODEL_REQUIRED: "请填写对话模型和相关性筛选模型的名称。",
      PROVIDER_ENDPOINT_INVALID: "Base URL 需为公网 HTTPS 接口地址，使用默认的 443 端口。",
      PROVIDER_ENDPOINT_FORBIDDEN: "接口地址需指向可访问的公网 HTTPS 模型服务。",
      PROVIDER_ENDPOINT_UNRESOLVABLE: "接口域名无法解析，请检查 Base URL。",
      TENANT_ACTION_HEADER_REQUIRED: "操作校验未通过，请刷新页面后重试。",
      TENANT_ORIGIN_REJECTED: "当前页面来源未通过校验，请从正式服务入口重新打开。",
      SYSTEM_TRIAL_EXHAUSTED: "系统默认模型的免费次数已用完，请配置自己的 API Key 和接口地址继续提问。",
      SYSTEM_MODEL_NOT_CONFIGURED: "系统默认模型尚未就绪，可以先配置自己的模型。"
    };
    const code = payload?.error?.code;
    if (messages[code]) return messages[code];
    if (status === 401) return messages.TENANT_AUTH_REQUIRED;
    if (status === 409) return "账号或配置已发生变化，请重新读取后再试。";
    if (status === 422 || status === 400) return "提交内容不符合要求，请检查用户名、密码或模型配置。";
    if (status === 429) return "操作过于频繁，请稍后重试。";
    // Never reflect a provider error or submitted credential into the page.
    return "操作暂时未完成，请稍后重试。";
  }

  function modelPayload(values, version) {
    const base_url = String(values.base_url || "").trim();
    const model = String(values.model || "").trim();
    const api_key = String(values.api_key || "").trim();
    const reranker_model = String(values.reranker_model || "").trim() || model;
    let address;
    try { address = new URL(base_url); } catch { throw new Error("请填写有效的 Base URL。"); }
    if (address.protocol !== "https:" || (address.port && address.port !== "443") || address.username || address.password || address.hash || address.search) {
      throw new Error("Base URL 需为公网 HTTPS 接口，使用默认 443 端口，且不含账号、密码或查询参数。");
    }
    if (!model) throw new Error("请填写对话模型名称。");
    if (!Number.isInteger(version) || version < 0) throw new Error("请先重新读取模型配置。");
    return { expected_version: version, base_url, model, reranker_model, ...(api_key ? { api_key } : {}) };
  }

  function trialSummary(config) {
    const trial = config?.trial;
    if (!trial || !Number.isInteger(trial.limit) || !Number.isInteger(trial.used)) return null;
    return { limit: trial.limit, used: trial.used, remaining: Math.max(0, trial.limit - trial.used),
      exhausted: config.source !== "personal" && trial.used >= trial.limit };
  }

  const helpers = { errorMessage, modelPayload, trialSummary };
  if (typeof module !== "undefined" && module.exports) module.exports = helpers;
  if (typeof document === "undefined") return;

  const $ = id => document.getElementById(id);
  const state = { account: null, config: null, authMode: "login", busy: false, dirty: false,
    loadSequence: 0, unavailable: false, refreshPending: null };
  const conversationKeys = ["libraryConversation", "chatConversation", "libraryPending", "chatPending"];
  const accountMarker = "interviewTenantActor";
  const fields = ["tenant-base-url", "tenant-model", "tenant-reranker-model", "tenant-api-key"];

  function message(value = "", error = false) {
    $("tenant-status").textContent = value;
    $("tenant-status").className = `notice ${error ? "error" : "info"}`;
    $("tenant-status").hidden = !value;
    if (value && $("tenant-dialog").open) $("tenant-status").scrollIntoView({ block: "start" });
  }

  async function request(path, { method = "GET", body } = {}) {
    let response;
    try {
      response = await fetch(new URL(path, document.baseURI), { method, credentials: "same-origin",
        headers: { ...(method !== "GET" ? { "X-Tenant-Action": "1" } : {}),
          ...(body !== undefined ? { "Content-Type": "application/json" } : {}) },
        ...(body !== undefined ? { body: JSON.stringify(body) } : {}) });
    } catch { throw new Error("无法连接服务，请稍后重试。"); }
    let payload;
    try { payload = await response.json(); } catch { throw new Error("服务响应无法读取，请刷新后重试。"); }
    if (!response.ok) {
      const error = new Error(errorMessage(payload, response.status));
      error.code = payload.error?.code;
      error.status = response.status;
      if (response.status === 401 && !/\/(login|register)$/.test(path)) await expireAccount();
      throw error;
    }
    return payload.data;
  }

  function clearConversations() {
    for (const key of conversationKeys) sessionStorage.removeItem(key);
  }

  function bindAccount(account) {
    if (!account?.enabled) return;
    const actor = account.authenticated ? account.tenant_id : "signed-out";
    if (sessionStorage.getItem(accountMarker) !== actor) clearConversations();
    sessionStorage.setItem(accountMarker, actor);
  }

  function banner(text = "", warning = false) {
    $("tenant-banner-text").textContent = text;
    $("tenant-banner").hidden = !text;
    $("tenant-banner").classList.toggle("warning", warning);
  }

  function renderAccount() {
    const account = state.account;
    const gated = state.unavailable || (account?.enabled && !account.authenticated);
    document.body.classList.toggle("tenant-auth-gated", Boolean(gated));
    $("tenant-auth-section").hidden = !account?.enabled || Boolean(account.authenticated);
    $("tenant-settings-section").hidden = !account?.authenticated;
    $("tenant-local-section").hidden = !account || account.enabled;
    $("tenant-account-label").textContent = account?.authenticated ? account.username : "账号与模型";
    if (state.unavailable) {
      $("tenant-account-caption").textContent = "账号状态暂不可用";
      banner("账号状态暂不可用，请稍后重试。当前对话仍保留在本次会话中。");
      $("tenant-banner-action").textContent = "重新读取 →";
    } else if (account?.enabled && !account.authenticated) {
      $("connection-text").textContent = "登录后连接工作台";
      $("connection-dot").className = "";
      $("tenant-account-caption").textContent = "登录 · 每账号免费 10 次";
      banner("登录或注册账号，保存自己的对话与复习记录，试用系统默认模型。每个账号免费 10 次 AI 提问。");
      $("tenant-banner-action").textContent = "登录 / 注册 →";
    } else if (account?.authenticated) {
      $("tenant-username-display").textContent = account.username;
      $("tenant-account-caption").textContent = "正在读取模型配置…";
      $("tenant-banner-action").textContent = "配置自己的模型 →";
      banner();
      if (state.config) renderModel(false);
    } else {
      $("tenant-account-caption").textContent = "本机工作区";
      banner();
    }
  }

  function renderModel(fillFields = true) {
    const config = state.config;
    if (!config) return;
    const trial = trialSummary(config);
    const personal = config.source === "personal";
    $("tenant-provider-badge").textContent = personal ? "使用自己的模型" : "使用系统默认模型";
    $("tenant-provider-badge").classList.toggle("personal", personal);
    $("tenant-trial-remaining").textContent = trial ? `${trial.remaining} 次剩余` : "额度暂不可用";
    $("tenant-trial-caption").textContent = trial ? `已使用 ${trial.used} / ${trial.limit} 次 · 每账号累计额度` : "请重新读取模型配置";
    $("tenant-account-caption").textContent = personal ? "个人模型 · 账号与模型" : trial ? `系统模型 · 剩余 ${trial.remaining} 次` : "账号与模型";
    $("tenant-model-reset").hidden = !personal;
    $("tenant-api-key").required = !personal || !config.api_key_configured;
    $("tenant-api-key").placeholder = personal && config.api_key_configured ? "留空保留已保存的 Key；填写可替换" : "输入你自己的 API Key";
    $("tenant-key-caption").textContent = personal && config.api_key_configured ?
      `已保存个人 Key${config.api_key_hint ? `（${config.api_key_hint}）` : ""}。留空保留，填写新的 Key 可替换；密钥不会回显。` :
      "请填写自己的 API Key。密钥仅保存到服务端，不保存在浏览器中，也不会回显。";
    if (fillFields) {
      $("tenant-base-url").value = personal ? config.base_url || "" : "";
      $("tenant-model").value = personal ? config.model || "" : "";
      $("tenant-reranker-model").value = personal ? config.reranker_model || "" : "";
      $("tenant-api-key").value = "";
      state.dirty = false;
    }
    if (trial?.exhausted) banner("系统默认模型的免费次数已用完。已保存的对话和复习记录仍可查看，配置自己的模型后可以继续 AI 提问。", true);
    else if (!personal && !config.system_configured) banner("系统默认模型尚未就绪，可以先配置自己的模型继续提问。", true);
    else banner();
  }

  async function refreshModel(fillFields = false) {
    if (!state.account?.authenticated) return;
    if (state.refreshPending) return state.refreshPending;
    const actor = state.account.tenant_id;
    state.refreshPending = (async () => {
      const data = await request("api/account/model");
      if (!state.account?.authenticated || state.account.tenant_id !== actor) return null;
      state.config = data;
      renderModel(fillFields && !state.dirty);
      return data;
    })();
    try { return await state.refreshPending; } finally { state.refreshPending = null; }
  }

  async function refresh() {
    const sequence = ++state.loadSequence;
    const recovering = state.unavailable;
    try {
      const account = await request("api/account");
      if (sequence !== state.loadSequence) return state.account;
      state.account = account;
      state.unavailable = false;
      bindAccount(account);
      renderAccount();
      if (account.authenticated) await refreshModel(true);
      if (recovering) location.reload();
      return account;
    } catch (error) {
      if (sequence !== state.loadSequence) return state.account;
      state.unavailable = !state.account;
      renderAccount();
      message(error.message, true);
      return state.account;
    }
  }

  async function expireAccount() {
    if (state.account?.enabled && !state.account.authenticated) return;
    ++state.loadSequence;
    state.account = { enabled: true, authenticated: false };
    state.config = null;
    state.dirty = false;
    $("tenant-api-key").value = "";
    $("tenant-password").value = "";
    bindAccount(state.account);
    renderAccount();
    root.dispatchEvent(new CustomEvent("interview:tenant-expired"));
  }

  function handleApiError(payload, status) {
    const code = payload?.error?.code;
    if (code === "TENANT_AUTH_REQUIRED") {
      expireAccount();
      message(errorMessage(payload, 401), true);
      open();
      return errorMessage(payload, 401);
    }
    if (code === "SYSTEM_TRIAL_EXHAUSTED") {
      if (state.config?.trial) state.config.trial.used = state.config.trial.limit;
      renderModel(false);
      refreshModel(false).catch(() => {});
      message(errorMessage(payload, status), true);
      open();
      return errorMessage(payload, status);
    }
    return null;
  }

  async function refreshSession() {
    if (state.busy || !state.account) return;
    try {
      const account = await request("api/account");
      if (account.enabled !== state.account.enabled ||
          (account.authenticated && account.tenant_id !== state.account.tenant_id)) {
        bindAccount(account);
        location.reload();
        return;
      }
      if (account.enabled && !account.authenticated && state.account.authenticated) await expireAccount();
      else if (account.authenticated) await refreshModel(false);
    } catch { /* A transient network failure keeps the current workspace intact. */ }
  }

  function setAuthMode(mode) {
    state.authMode = mode;
    const register = mode === "register";
    $("tenant-login-tab").setAttribute("aria-selected", String(!register));
    $("tenant-register-tab").setAttribute("aria-selected", String(register));
    $("tenant-auth-submit").textContent = register ? "注册并开始使用" : "登录";
    $("tenant-password").autocomplete = register ? "new-password" : "current-password";
    $("tenant-auth-description").textContent = register ? "每个账号免费 10 次 AI 提问，用完后可使用自己的模型。" : "登录后继续上次的对话。";
    message();
  }

  function setBusy(busy) {
    state.busy = busy;
    for (const id of ["tenant-auth-submit", "tenant-login-tab", "tenant-register-tab", "tenant-model-save", "tenant-model-reset", "tenant-model-refresh", "tenant-logout", ...fields]) $(id).disabled = busy;
    $("tenant-auth-form").setAttribute("aria-busy", String(busy));
    $("tenant-model-form").setAttribute("aria-busy", String(busy));
  }

  function open() {
    if (!$("tenant-dialog").open) $("tenant-dialog").showModal();
    if (state.unavailable) refresh();
    else if (state.account?.authenticated && !state.config) refreshModel(true).catch(error => message(error.message, true));
  }

  $("tenant-open").addEventListener("click", open);
  $("tenant-banner-action").addEventListener("click", open);
  $("tenant-close").addEventListener("click", () => $("tenant-dialog").close());
  $("tenant-dialog").addEventListener("close", () => { $("tenant-api-key").value = ""; $("tenant-password").value = ""; });
  $("tenant-login-tab").addEventListener("click", () => setAuthMode("login"));
  $("tenant-register-tab").addEventListener("click", () => setAuthMode("register"));
  for (const id of fields) $(id).addEventListener("input", () => { state.dirty = true; });
  $("tenant-auth-form").addEventListener("submit", async event => {
    event.preventDefault();
    if (state.busy) return;
    setBusy(true); message();
    try {
      const account = await request(`api/account/${state.authMode}`, { method: "POST",
        body: { username: $("tenant-username").value.trim(), password: $("tenant-password").value } });
      bindAccount(account);
      $("tenant-password").value = "";
      location.reload();
    } catch (error) { message(error.message, true); }
    finally { $("tenant-password").value = ""; setBusy(false); }
  });
  $("tenant-logout").addEventListener("click", async () => {
    if (state.busy) return;
    setBusy(true); message();
    try {
      await request("api/account/logout", { method: "POST" });
      clearConversations(); sessionStorage.setItem(accountMarker, "signed-out");
      $("tenant-api-key").value = "";
      location.reload();
    } catch (error) { message(error.message, true); }
    finally { setBusy(false); }
  });
  $("tenant-model-form").addEventListener("submit", async event => {
    event.preventDefault();
    if (state.busy) return;
    setBusy(true); message();
    try {
      const body = modelPayload({ base_url: $("tenant-base-url").value, model: $("tenant-model").value,
        reranker_model: $("tenant-reranker-model").value, api_key: $("tenant-api-key").value }, state.config?.version);
      const data = await request("api/account/model", { method: "PATCH", body });
      state.config = data; state.dirty = false; renderModel();
      message("个人模型配置已保存，接下来的 AI 提问将使用你的模型。");
    } catch (error) { message(error.message, true); }
    finally { $("tenant-api-key").value = ""; setBusy(false); }
  });
  $("tenant-model-reset").addEventListener("click", async () => {
    if (state.busy || !state.config) return;
    setBusy(true); message();
    try {
      state.config = await request(`api/account/model?expected_version=${state.config.version}`, { method: "DELETE" });
      state.dirty = false; renderModel(); message("已恢复系统默认模型，已使用的免费次数保持不变。");
    } catch (error) { message(error.message, true); }
    finally { $("tenant-api-key").value = ""; setBusy(false); }
  });
  $("tenant-model-refresh").addEventListener("click", async () => {
    if (state.busy) return;
    setBusy(true); message(); state.dirty = false;
    try { await refreshModel(true); message("已读取最新模型配置。"); }
    catch (error) { message(error.message, true); }
    finally { setBusy(false); }
  });
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) refreshSession();
  });
  root.addEventListener("focus", refreshSession);

  root.InterviewTenant = { ...helpers, ready: refresh(), open, refreshModel, handleApiError,
    canUseWorkspace: () => !state.unavailable && Boolean(state.account && (!state.account.enabled || state.account.authenticated)),
    requireAccount: () => { if (state.account?.enabled && !state.account.authenticated) { open(); return false; } return !state.unavailable; } };
})(typeof window !== "undefined" ? window : globalThis);
