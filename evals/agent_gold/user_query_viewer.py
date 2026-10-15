"""Build an offline, source-bound browser for frozen user-query gold."""
from __future__ import annotations

import argparse
import base64
import hashlib
import html
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

from eval.common import digest, inside, read_json, write_json
from eval.validate_gold import require
from evals.agent_gold.user_query_source_20261015 import validate_dataset
from interview_intelligence.ingestion.snapshot import decode_source


DEFAULT_DATASET = Path("data/gold/user-query-source-reviewed-20261015-v2")
DEFAULT_OUTPUT = Path("data/reports/user-query-source-reviewed-20261015-v1/browser-v2")
LINK_FIELD = re.compile(r"^\s*[-*]?\s*(?:链接|原文链接|来源链接|原帖链接|原文|来源|原帖|source(?:_url)?|url)\s*[:：]", re.I)
HTTP_URL = re.compile(r"https?://[^\s<>\"'\)\]]+", re.I)


def safe_url(value):
    if not isinstance(value, str) or value != value.strip() or any(c.isspace() or ord(c) < 32 for c in value):
        return None
    if any(c in value for c in "\\<>\"'"):
        return None
    try:
        parsed = urlsplit(value)
        return value if (parsed.scheme.lower() in {"http", "https"} and parsed.hostname
                         and parsed.username is None and parsed.password is None) else None
    except ValueError:
        return None


def original_urls(text, source_hash, source_path):
    """Expose only explicit URL metadata fields, never infer a source website."""
    urls, offset = [], 0
    for line in text.splitlines(keepends=True):
        if LINK_FIELD.match(line):
            for match in HTTP_URL.finditer(line):
                value = safe_url(match.group())
                if value and value not in {entry["url"] for entry in urls}:
                    urls.append({"url": value, "evidence": {"source_hash": source_hash,
                        "source_path": source_path, "start_char": offset + match.start(),
                        "end_char": offset + match.end(), "quote": match.group()}})
        offset += len(line)
    return urls


def embedded_json(value):
    # application/json still lives in an HTML raw-text element: a literal
    # </script> must never be allowed to terminate it.
    return (json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            .replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
            .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))


def viewer_payload(dataset, bundle):
    manifest = bundle["manifest"]
    history = read_json(inside(dataset, manifest["history_path"]))
    sources = {}
    questions = []
    for question in bundle["questions"]:
        source_path, sha = question["source_path"], question["source_hash"]
        if source_path not in sources:
            raw = inside(dataset, source_path).read_bytes()
            require(digest(raw) == sha, "VIEWER_SOURCE_HASH_MISMATCH")
            text = decode_source(raw)
            sources[source_path] = (sha, text, original_urls(text, sha, source_path))
        verified_sha, text, urls = sources[source_path]
        require(verified_sha == sha, "VIEWER_SOURCE_IDENTITY_CHANGED")
        for span in question["source_spans"] + question.get("context_spans", []):
            require(text[span["start_char"]:span["end_char"]] == span["quote"], "VIEWER_SOURCE_SPAN_MISMATCH")
        questions.append({**question, "original_urls": urls})
    provenance = {"dataset_id": manifest["dataset_id"], "snapshot": manifest["snapshot"],
        "manifest_sha256": digest(manifest), "manifest_file_sha256": digest((dataset / "manifest.json").read_bytes()),
        "freeze_sha256": digest((dataset / "freeze.json").read_bytes()), "bundle_sha256": digest(bundle),
        "human_verified": False, "agent_verified": True, "release_eligible": False,
        "scope_note": "Agent 审核的已见来源诊断；不是人工认证、模型预测或整体发布结论。",
        "url_note": "原站链接仅取已校验原文中的显式链接字段；未联网检查可达性。"}
    return {"provenance": provenance, "questions": questions, "scopes": bundle["scopes"],
            "cases": bundle["cases"], "history": history["entries"]}, len(sources)


SCRIPT = r"""
'use strict';
const data=JSON.parse(document.getElementById('gold-data').textContent);
const $=id=>document.getElementById(id), labels={ACCEPT:'正例',REJECT:'负例',BOUNDARY:'边界',UNREVIEWED:'未审核'};
const node=(tag,text,cls)=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=String(text);if(cls)e.className=cls;return e;};
function section(parent,title,text){parent.append(node('h4',title),node('pre',text));}
function details(parent,title,text){const e=node('details');e.append(node('summary',title),node('pre',text));parent.append(e);}
function spanText(spans){return spans.map(s=>'行 '+(s.start_line??'?')+'–'+(s.end_line??'?')+' / 字符 '+s.start_char+'–'+s.end_char+'\n'+s.quote).join('\n\n');}
function addLink(parent,value){try{const u=new URL(value);if(!['http:','https:'].includes(u.protocol))return;const a=node('a',value);a.href=u.href;a.target='_blank';a.rel='noopener noreferrer';parent.append(a);}catch{}}
function grade(q,s){return q.scope_judgments[s]?.grade??'UNREVIEWED';}
for(const id of Object.keys(data.scopes))$('scope').append(new Option(id,id));
$('scope').value='engineering_system_design';
$('provenance').textContent=JSON.stringify(data.provenance,null,2);
let page=0;const pageSize=40;
function renderQuestions(){
 const sid=$('scope').value,g=$('grade').value,term=$('search').value.trim().toLocaleLowerCase();
 const matched=data.questions.filter(q=>(g==='ALL'||grade(q,sid)===g)&&(!term||JSON.stringify([q.raw_question,q.original_path,q.id,q.occurrence_id,q.canonical_question_id,q.source_revision_id,q.scope_judgments[sid]]).toLocaleLowerCase().includes(term)));
 const pages=Math.max(1,Math.ceil(matched.length/pageSize));page=Math.min(page,pages-1);
 const box=$('questions');box.replaceChildren();
 for(const q of matched.slice(page*pageSize,(page+1)*pageSize)){
  const card=node('article'),status=grade(q,sid);card.append(node('div',labels[status]+' · '+q.id+(q.source_only?' · 原文存在但未抽取':'')+' · '+(q.review.evidence_level??''),'badge '+status));
  card.append(node('h3',q.raw_question));
  section(card,'审核理由',q.scope_judgments[sid]?.basis??'本 scope 未审核；不能作负例。');
  card.append(node('p','来源：'+q.original_path));
  if(q.original_urls.length){const links=node('div',undefined,'links');for(const link of q.original_urls)addLink(links,link.url);card.append(links);details(card,'原站 URL 字段的原文依据',JSON.stringify(q.original_urls,null,2));}
  else card.append(node('p','没有可验证的原站链接字段。','muted'));
  section(card,'原文题干 quote',spanText(q.source_spans));
  details(card,'展开原文上下文（不将邻题任务加入本题）',q.context_spans?.length?spanText(q.context_spans):'无已审核上下文窗口；该条为原问级筛查。');
  details(card,'身份与审核记录',JSON.stringify({occurrence_id:q.occurrence_id,canonical_question_id:q.canonical_question_id,source_revision_id:q.source_revision_id,source_hash:q.source_hash,source_path:q.source_path,reviewed_company:q.reviewed_company,design_family_id:q.design_family_id,review:q.review},null,2));
  box.append(card);
 }
 const s=data.scopes[sid];$('totals').textContent='命中 '+matched.length+' 条；第 '+(page+1)+' / '+pages+' 页。已入库审核：正 '+s.positive_gold_ids.length+' / 负 '+s.negative_gold_ids.length+' / 边界 '+s.boundary_gold_ids.length+'；另有原文漏抽：范围正例 '+(s.source_only_positive_gold_ids?.length??0)+' / 范围待裁 '+(s.source_only_boundary_gold_ids?.length??0)+'。认证：'+s.certification+'。';
 $('prev').disabled=page===0;$('next').disabled=page===pages-1;
}
function renderCases(){
 const box=$('cases');box.replaceChildren();
 for(const c of data.cases){
  const card=node('article');card.append(node('h3',c.id));
  for(const t of c.turns){
   const o=t.origin,h=data.history[Number(o.history_id.replace('history-',''))],m=h?.messages.find(m=>m.message_id===o.message_id);
   card.append(node('h4','第 '+(t.turn_index+1)+' 轮 · '+(o.kind==='historical_user_substring'?'历史原问（逐字片段）':'衍生变体')));
   card.append(node('pre',t.message));
   card.append(node('p','关联历史：'+(h?.thread_title??'')+' / '+o.history_id));
   if(o.derivation)card.append(node('p','衍生依据：'+o.derivation));
   if(o.quote)details(card,'历史锚点（字符 '+o.start_char+'–'+o.end_char+'）',o.quote);
   details(card,'原始用户消息与身份',JSON.stringify({thread_id:o.thread_id,turn_id:o.turn_id,message_id:o.message_id,text:m?.text??''},null,2));
   details(card,'冻结 expected（页面相关性与完整性认证分开）',JSON.stringify(t.expected,null,2));
  }
  box.append(card);
 }
}
for(const id of ['scope','grade','search'])$(id).addEventListener(id==='search'?'input':'change',()=>{page=0;renderQuestions();});
$('prev').onclick=()=>{page--;renderQuestions();};$('next').onclick=()=>{page++;renderQuestions();};
for(const id of ['show-questions','show-cases'])$(id).onclick=()=>{const qs=id==='show-questions';$('question-panel').hidden=!qs;$('case-panel').hidden=qs;$(id).setAttribute('aria-pressed','true');$(qs?'show-cases':'show-questions').setAttribute('aria-pressed','false');};
renderQuestions();renderCases();
"""


def render_html(payload):
    script_hash = base64.b64encode(hashlib.sha256(SCRIPT.encode("utf-8")).digest()).decode("ascii")
    title = html.escape(payload["provenance"]["dataset_id"], quote=True)
    return """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'sha256-""" + script_hash + """'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>真实面经 Gold 浏览 · """ + title + """</title><style>
:root{font:15px/1.6 system-ui,sans-serif;color:#172b3a;background:#f4f6f8}body{max-width:1100px;margin:auto;padding:24px}h1{font-size:26px;margin-bottom:4px}h3{font-size:18px;white-space:pre-wrap}h4{margin:14px 0 5px}p{margin:8px 0}nav,.controls,.pager{display:flex;gap:12px;flex-wrap:wrap;align-items:center;margin:18px 0}button,select,input{font:inherit;padding:8px 12px;border:1px solid #b6c5d0;border-radius:6px;background:white}input{min-width:260px;flex:1}button{cursor:pointer}button[aria-pressed=true]{background:#173c51;color:white}button:disabled{opacity:.4;cursor:default}article{background:white;border:1px solid #dbe3e8;border-radius:10px;padding:20px;margin:16px 0}pre{font:13px/1.6 ui-monospace,monospace;white-space:pre-wrap;overflow-wrap:anywhere;margin:4px 0;color:#263e4e}summary{cursor:pointer;color:#1b617b}details{margin-top:12px}.notice{border-left:4px solid #a57520;padding:12px 16px;background:#fff9e9}.badge{font-size:13px;font-weight:600}.ACCEPT{color:#166249}.REJECT{color:#8d3938}.BOUNDARY{color:#8b6618}.muted,#totals{color:#5c6c77}.links{display:flex;flex-direction:column;overflow-wrap:anywhere}a{color:#126480}[hidden]{display:none!important}
</style></head><body><header><h1>真实面经 Gold 浏览</h1><p>""" + title + """ · 可离线打开</p>
<p class="notice">Agent 审核，human_verified=false。已见 r293 来源诊断，不代表新来源泛化、模型成绩或整体发布通过。边界与未审核条目不自动当作负例。原站链接来自原文显式字段，未联网检查可达性。</p>
<details><summary>冻结身份与数据边界</summary><pre id="provenance"></pre></details></header>
<nav><button id="show-questions" aria-pressed="true">面经原题</button><button id="show-cases" aria-pressed="false">24 个查询用例 / 34 轮</button></nav>
<main><section id="question-panel"><div class="controls"><label>Scope <select id="scope"></select></label><label>审核 <select id="grade"><option value="ACCEPT">正例</option><option value="REJECT">负例</option><option value="BOUNDARY">边界</option><option value="UNREVIEWED">未审核</option><option value="ALL">全部</option></select></label><input id="search" aria-label="关键词或身份搜索" placeholder="搜索题干、来源、理由或 ID"></div><p id="totals"></p><div class="pager"><button id="prev">上一页</button><button id="next">下一页</button><span>每页 40 条</span></div><div id="questions"></div></section>
<section id="case-panel" hidden><h2>历史原问与衍生关系</h2><p>每轮注明历史消息锚点与衍生说明；expected 是冻结的审核合同，不是实际模型输出。</p><div id="cases"></div></section></main>
<script id="gold-data" type="application/json">""" + embedded_json(payload) + """</script><script>""" + SCRIPT + """</script></body></html>"""


def build(dataset=DEFAULT_DATASET, output=DEFAULT_OUTPUT):
    dataset, output = Path(dataset), Path(output)
    bundle = validate_dataset(dataset)
    # Derived browsing files must never be written inside the frozen input.
    require(not output.resolve().is_relative_to(dataset.resolve()), "VIEWER_OUTPUT_INSIDE_FROZEN_GOLD")
    payload, source_count = viewer_payload(dataset, bundle)
    document = render_html(payload)
    output.mkdir(parents=True, exist_ok=True)
    (output / "index.html").write_text(document, encoding="utf-8", newline="\n")
    audit = {"schema": "user_query_gold_viewer_v1", **payload["provenance"],
        "questions": len(payload["questions"]), "sources": source_count, "cases": len(payload["cases"]),
        "turns": sum(len(c["turns"]) for c in payload["cases"]),
        "original_url_source_count": len({q["source_hash"] for q in payload["questions"] if q["original_urls"]}),
        "scope_counts": {key: {"positive": len(s["positive_gold_ids"]), "negative": len(s["negative_gold_ids"]),
                              "boundary": len(s["boundary_gold_ids"]),
                              "source_only_positive": len(s.get("source_only_positive_gold_ids", [])),
                              "source_only_boundary": len(s.get("source_only_boundary_gold_ids", []))}
                         for key, s in payload["scopes"].items()},
        "html_sha256": digest((output / "index.html").read_bytes()), "generator_sha256": digest(Path(__file__).read_bytes())}
    write_json(output / "audit.json", audit)
    return audit


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    print(json.dumps(build(args.dataset, args.output), ensure_ascii=False, indent=2))
