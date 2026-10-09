import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { errorMessage, modelPayload, trialSummary } = require("../../src/interview_intelligence/web/assets/tenant.js");

test("editing an existing provider without a new key preserves the server credential", () => {
  const payload = modelPayload({ base_url: " https://api.example.com/v1 ", model: " qwen-test ", api_key: "", reranker_model: "" }, 3);
  assert.deepEqual(payload, { expected_version: 3, base_url: "https://api.example.com/v1", model: "qwen-test", reranker_model: "qwen-test" });
  assert.equal(Object.hasOwn(payload, "api_key"), false);
  const updated = modelPayload({ ...payload, api_key: " own-new-key ", reranker_model: "qwen-rerank" }, 3);
  assert.equal(updated.api_key, "own-new-key");
  assert.equal(updated.reranker_model, "qwen-rerank");
});

test("provider addresses reject credentials, secret query parameters and unsupported endpoints before submission", () => {
  for (const base_url of ["http://api.example.com/v1", "https://api.example.com:8080/v1", "https://user:secret@api.example.com/v1", "https://api.example.com/v1?key=secret", "https://api.example.com/v1#secret", "javascript:alert(1)"]) {
    assert.throws(() => modelPayload({ base_url, model: "qwen" }, 1), /Base URL/);
  }
  assert.throws(() => modelPayload({ base_url: "https://api.example.com/v1", model: "qwen" }, undefined), /重新读取/);
});

test("provider failures never echo backend messages containing credentials", () => {
  const secret = "secret-key-that-must-not-render";
  for (const status of [400, 401, 409, 422, 429, 500, 502]) {
    const message = errorMessage({ error: { code: "UNKNOWN_PROVIDER_ERROR", message: secret } }, status);
    assert.equal(message.includes(secret), false);
  }
  assert.match(errorMessage({ error: { code: "TENANT_LOGIN_INVALID" } }, 401), /用户名或密码/);
  assert.match(errorMessage({ error: { code: "PROVIDER_VERSION_CONFLICT" } }, 409), /重新读取/);
  assert.match(errorMessage({ error: { code: "SYSTEM_TRIAL_EXHAUSTED" } }, 429), /配置自己的 API Key/);
});

test("exhausted trial is a lifetime system quota and does not block a personal provider", () => {
  const exhausted = { source: "system", trial: { limit: 10, used: 10, remaining: 99 } };
  assert.deepEqual(trialSummary(exhausted), { limit: 10, used: 10, remaining: 0, exhausted: true });
  assert.deepEqual(trialSummary({ ...exhausted, source: "personal" }), { limit: 10, used: 10, remaining: 0, exhausted: false });
  assert.equal(trialSummary({ source: "system", trial: { limit: 10, used: 9 } }).remaining, 1);
  assert.equal(trialSummary(null), null);
});
