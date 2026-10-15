const test = require("node:test");
const assert = require("node:assert/strict");
const view = require("../../src/interview_intelligence/web/assets/agent-observability.js");

test("unknown observations are not displayed as zero", () => {
  assert.equal(view.duration(null), "未知");
  assert.equal(view.duration(undefined), "未知");
  assert.equal(view.percent(null), "—");
  assert.equal(view.percent(0), "0.0%");
  assert.equal(view.duration(0), "0 ms");
});
test("deep links select only supported task types and bounded identifiers", () => {
  assert.deepEqual(view.route("#agent/QUERY/run-123"), {kind:"QUERY", id:"run-123"});
  assert.equal(view.route("#agent/QUERY/../../account"), null);
  assert.equal(view.route("#agent/OTHER/run-123"), null);
});
test("tool and agent failures retain an explicit timeline label", () => {
  assert.equal(view.eventLabel({type:"pi_event", data:{type:"tool_execution_end", isError:true}}), "Agent 工具调用失败");
  assert.equal(view.eventLabel({type:"tool_error", data:{}}), "工具调用失败");
  assert.equal(view.eventLabel({type:"tool_error", data:{status:"CANCELLED"}}), "工具调用已取消");
  assert.equal(view.eventLabel({type:"tool_error", data:{status:"INTERRUPTED"}}), "工具调用已中断");
  assert.equal(view.eventLabel({type:"stage", data:{stage:"tool",tool:"search_questions"}}), "执行工具 · search_questions");
});
