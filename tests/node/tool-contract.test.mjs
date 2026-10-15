import test from "node:test";
import assert from "node:assert/strict";
import {createHash} from "node:crypto";
import {readFile} from "node:fs/promises";
import http from "node:http";
import {createModels, fauxProvider, fauxAssistantMessage, fauxToolCall} from "@earendil-works/pi-ai";
import {runQuery} from "../../services/pi-agent/runtime.mjs";

// Captured before contract consolidation from the real Python query_model_schema
// and the pinned Pi model boundary. No test-time fixture regeneration is allowed.
const before=JSON.parse(await readFile(new URL("../fixtures/query-tool-contract-before.json",import.meta.url),"utf8"));

test("the pinned Pi exposes the complete frozen tool menu without contract drift",async t=>{
  const plan={action:"LIST",filters:Object.fromEntries(Object.entries(before.input_schema.$defs.FilterSpec.properties)
    .map(([key,value])=>[key,value.default])),sort:"frequency",top_n:null,page_size:20,final:true};
  const requests=[],menus=[];
  const server=http.createServer(async(req,res)=>{
    let body="";for await(const chunk of req)body+=chunk;
    requests.push({url:req.url,authorization:req.headers.authorization,plan:JSON.parse(body)});
    res.writeHead(200,{"Content-Type":"application/json"});
    res.end(JSON.stringify({answer:"Synthetic contract fixture",facts:{data:[],meta:{route:"SQL"}}}));
  });
  await new Promise(resolve=>server.listen(0,"127.0.0.1",resolve));
  t.after(()=>new Promise(resolve=>server.close(resolve)));
  const faux=fauxProvider();faux.setResponses([fauxAssistantMessage(fauxToolCall("list_questions",plan))]);
  const models=createModels();models.setProvider(faux.provider);

  const result=await runQuery({run_id:"contract-before",schema:structuredClone(before.input_schema),
    prompt:"Use the list tool.",context:{message:"List synthetic questions."}},
    {hostUrl:`http://127.0.0.1:${server.address().port}`,token:"contract-fixture-token",streamFn:(_model,context,options)=>{
      const tools=new Map();
      for(const message of context.messages.filter(message=>message.role==="system")) {
        for(const tool of message.toolsRemoved || []) tools.delete(typeof tool==="string" ? tool : tool.name);
        for(const tool of message.toolsAdded || []) tools.set(tool.name,JSON.parse(JSON.stringify(tool)));
      }
      menus.push([...tools.values()]);
      return models.streamSimple(faux.getModel(),context,options);
    }});

  assert.equal(result.completed,true);
  assert.equal(result.provider_calls,1);
  assert.equal(result.tool_calls,1);
  assert.equal(menus.length,1);
  assert.equal(menus[0].length,8);
  // Pi's declaration includes description, complete parameters and sampling policy;
  // display-only label and executable callbacks are intentionally absent here.
  assert.deepEqual(menus[0].map(tool=>({name:tool.name,
    sha256:createHash("sha256").update(JSON.stringify(tool)).digest("hex")})),before.tools);
  assert.deepEqual(requests,[{url:"/internal/agent/runs/contract-before/tools/list_questions",
    authorization:"Bearer contract-fixture-token",plan}]);
});
