import test from "node:test";
import assert from "node:assert/strict";
import http from "node:http";
import {createModels, fauxProvider, fauxAssistantMessage, fauxToolCall} from "@earendil-works/pi-ai";
import {runQuery} from "../../services/pi-agent/runtime.mjs";

test("Pi observations correlate validated and rejected attempts without losing arguments", async t => {
  const server = http.createServer(async (request, response) => {
    for await (const chunk of request) { /* Drain the domain request. */ }
    response.writeHead(200, {"Content-Type":"application/json"});
    response.end(JSON.stringify({answer:"完成", facts:{meta:{}, data:[]}}));
  });
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  t.after(() => new Promise(resolve => server.close(resolve)));
  const faux = fauxProvider();
  faux.setResponses([
    fauxAssistantMessage(fauxToolCall("list_questions", {action:"LIST"})),
    fauxAssistantMessage(fauxToolCall("list_questions", {action:"LIST", page_size:20})),
  ]);
  const models = createModels(); models.setProvider(faux.provider);
  const observed = [];
  await runQuery({run_id:"observation-test", context:{message:"列出题目"}, prompt:"Use tools",
    schema:{type:"object", properties:{action:{type:"string"}, page_size:{type:"integer"}},
      required:["action","page_size"]}}, {
    hostUrl:`http://127.0.0.1:${server.address().port}`, token:"test-only-internal-token",
    streamFn:(_model, context, options) => models.streamSimple(faux.getModel(), context, options),
    onEvent:async event => observed.push(event),
  });
  const starts = observed.filter(event => event.type === "tool_execution_start");
  const ends = observed.filter(event => event.type === "tool_execution_end");
  assert.equal(starts.length, 2); assert.equal(ends.length, 2);
  assert.equal(starts[0].args.action, "LIST");
  assert.equal(starts[1].args.page_size, 20);
  assert.ok(starts.every(event => event.toolCallId));
  assert.equal(starts[0].toolCallId, ends[0].toolCallId);
  assert.equal(ends[0].isError, true); assert.ok(ends[0].error);
  assert.equal(ends[1].isError, false);
  assert.ok(!JSON.stringify(observed).includes("test-only-internal-token"));
});
