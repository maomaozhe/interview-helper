import test from "node:test";
import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";
import vm from "node:vm";

test("engineering rows keep pending questions visible without confirmed frequency or unsafe HTML",async()=>{
  const source=await readFile(new URL("../../src/interview_intelligence/web/assets/app.js",import.meta.url),"utf8");
  const body=source.slice(source.indexOf("function semanticReviewHTML("),source.indexOf("function usageFilters("))
    + source.slice(source.indexOf("function resultRow("),source.indexOf("let searchController"));
  const context={state:{topicLabels:{}},labels:{},escapeHTML:value=>String(value).replace(/[&<>\"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"})[c])};
  vm.createContext(context);vm.runInContext(body,context);
  const html=context.rowHTML({canonical_question_id:"unknown",canonical_text:"设计服务",
    semantic_verification:"PENDING",confirmed_occurrence_count:0,pending_occurrence_count:2,
    occurrence_count:2,pending_sources:[{raw_question:"<script>unknown</script>",reason:"原文缺少条件"}]},0);
  assert.match(html,/待核验工程设计归属/);
  assert.match(html,/另有 2 次提问待核验/);
  assert.match(html,/<strong>0<\/strong><small>已确认提问/);
  assert.match(html,/原文缺少条件/);assert.match(html,/&lt;script&gt;/);
  assert.doesNotMatch(html,/<script>/);
  const mixed=context.rowHTML({canonical_question_id:"mixed",canonical_text:"设计订单",
    semantic_verification:"CONFIRMED",confirmed_occurrence_count:3,pending_occurrence_count:1,
    pending_sources:[{raw_question:"追问条件缺失",reason:"未决"}]},1);
  assert.match(mixed,/已确认工程设计/);assert.match(mixed,/另有 1 次提问待核验/);
  assert.match(mixed,/<strong>3<\/strong><small>已确认提问/);
  const group=context.resultRow({key:"公司A",semantic_verification:"PENDING",
    confirmed_occurrence_count:0,pending_occurrence_count:2,
    pending_sources:[{raw_question:"分组待核验原问",reason:"缺少构建目标"}]},0);
  assert.match(group,/待核验工程设计归属/);assert.match(group,/另有 2 次提问待核验/);
  assert.match(group,/分组待核验原问/);assert.match(group,/缺少构建目标/);
  assert.match(group,/<strong>0<\/strong><small>已确认提问/);
  const issue=context.sourceIssuesHTML([{raw_question:"保留原问 <bad>",reason:"来源位置无效",source_evidence_verified:false}]);
  assert.match(issue,/保留原问 &lt;bad&gt;/);assert.match(issue,/暂不能作为已核验引用/);
  assert.doesNotMatch(issue,/data-source-index|查看完整原文|第 1 行/);
});
