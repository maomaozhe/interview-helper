import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { chartModel, date, errorMessage, policyPayload } = require("../../src/interview_intelligence/web/assets/admin.js");

test("QPS chart positions irregular time buckets by time and preserves zero buckets", () => {
  const data = [
    { timestamp: "2026-10-09T00:01:00Z", qps: 1, requests: 60 },
    { timestamp: "2026-10-09T00:10:00Z", qps: 2, requests: 120 },
    { timestamp: "2026-10-09T00:00:00Z", qps: 0, requests: 0 },
  ];
  const chart = chartModel(data);
  assert.equal(chart.points.length, 3);
  assert.equal(chart.points[0].qps, 0);
  assert.equal(chart.points[0].y, chart.bottom);
  assert.equal(chart.points[1].x, chart.left + (chart.right - chart.left) / 10);
  assert.equal(chart.points[2].y, chart.top);
  assert.ok(chart.area.endsWith(" Z"));
  assert.deepEqual(data.map(item => item.qps), [1, 2, 0], "chart must not reorder its API input");
});

test("empty, zero and singleton QPS charts have finite honest scales", () => {
  for (const input of [[], [{ timestamp: "2026-10-09T00:00:00Z", qps: 0 }], [{ timestamp: "2026-10-09T00:00:00Z", qps: .003 }]]) {
    const chart = chartModel(input);
    assert.equal(chart.points.length, input.length);
    assert.ok(chart.ceiling > 0);
    assert.doesNotMatch(chart.line, /NaN|Infinity/);
    assert.doesNotMatch(chart.area, /NaN|Infinity/);
    for (const point of chart.points) assert.ok(point.x >= chart.left && point.x <= chart.right && point.y >= chart.top && point.y <= chart.bottom);
  }
});

test("invalid QPS or timestamps are discarded instead of displaying misleading values", () => {
  const result = chartModel([
    { timestamp: "invalid", qps: 10 }, { timestamp: "2026-10-09T00:00:00Z", qps: -2 },
    { timestamp: "2026-10-09T00:00:00Z", qps: Infinity }, { timestamp: "2026-10-09T00:00:00Z", qps: "2" },
    { timestamp: "2026-10-09T00:00:00Z", qps: 2 }
  ]);
  assert.equal(result.points.length, 1);
  assert.equal(result.points[0].qps, 2);
});

test("timestamps use Beijing time at a UTC date boundary regardless of host time zone", () => {
  assert.match(date("2026-10-08T16:03:04Z"), /10\/09.*00:03:04/);
  assert.equal(date("invalid"), "—");
  assert.equal(date(null), "—");
});

test("real backend uppercase admin errors receive actionable Chinese messages", () => {
  assert.match(errorMessage({ error: { code: "ADMIN_NOT_CONFIGURED" } }, 503), /ADMIN_ACCESS_TOKEN/);
  assert.match(errorMessage({ detail: "POLICY_VERSION_CONFLICT" }, 409), /其他管理员/);
  assert.match(errorMessage({ error: { code: "ADMIN_LOGIN_RATE_LIMITED" } }, 429), /稍后重试/);
  assert.match(errorMessage({ detail: "ADMIN_ACTION_HEADER_REQUIRED" }, 403), /刷新页面/);
});

test("policy saves preserve explicit off switches and include loaded version", () => {
  const result = policyPayload({ quota_enabled: false, quota_limit: "10", quota_period: "DAY", quota_scope: "IP", rate_enabled: true, rate_per_minute: "60" }, 7);
  assert.deepEqual(result, { expected_version: 7, quota_enabled: false, quota_limit: 10, quota_period: "DAY", quota_scope: "IP", rate_enabled: true, rate_per_minute: 60 });
});
