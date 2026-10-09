const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assetPath = path.resolve(__dirname, '../../src/interview_intelligence/web/assets');
const {renderMarkdown} = require(path.join(assetPath, 'markdown.js'));

test('answers render headings, emphasis, quotes, nested lists and links', () => {
  const html = renderMarkdown('# 回答\n\n**重点**、*解释*和~~旧方案~~\n\n> 引用\n\n1. 第一步\n   - 嵌套条目\n\n[资料](https://example.com/docs?a=1&b=2)');
  assert.match(html, /<h1>回答<\/h1>/);
  assert.match(html, /<strong>重点<\/strong>/);
  assert.match(html, /<em>解释<\/em>/);
  assert.match(html, /<s>旧方案<\/s>/);
  assert.match(html, /<blockquote>/);
  assert.match(html, /<ol>[\s\S]*<ul>[\s\S]*嵌套条目/);
  assert.match(html, /href="https:\/\/example\.com\/docs\?a=1&amp;b=2"/);
  assert.match(html, /target="_blank" rel="noopener noreferrer"/);
});

test('fenced and inline code remain escaped, with a language class', () => {
  const html = renderMarkdown('用 `Map<K, V>` 保存\n\n```js\nconst a = "<script>alert(1)</script>";\n```');
  assert.match(html, /<code>Map&lt;K, V&gt;<\/code>/);
  assert.match(html, /<pre><code class="language-js">/);
  assert.match(html, /&lt;script&gt;alert\(1\)&lt;\/script&gt;/);
  assert.doesNotMatch(html, /<script>/);
});

test('GFM tables retain alignment and Chinese text', () => {
  const html = renderMarkdown('| 方案 | 成本 |\n| :--- | ---: |\n| Redis | 低 |');
  assert.match(html, /<table>/);
  assert.match(html, /<th style="text-align:left">方案<\/th>/);
  assert.match(html, /<td style="text-align:right">低<\/td>/);
});

test('raw HTML and event handlers never create elements', () => {
  const html = renderMarkdown('<script>alert(1)</script>\n<img src=x onerror=alert(2)>\n<svg onload=alert(3)>');
  assert.match(html, /&lt;script&gt;/);
  assert.match(html, /&lt;img/);
  assert.doesNotMatch(html, /<(?:script|img|svg)\b/i);
});

test('unsafe and obfuscated URL schemes are rejected for links and images', () => {
  for (const url of ['javascript:alert(1)', 'JaVaScRiPt:alert(1)', 'java&#x73;cript:alert(1)',
    'javascript&#58;alert(1)', 'vbscript:msgbox(1)', 'file:///etc/passwd',
    'data:text/html;base64,PHNjcmlwdD4=', 'data:image/svg+xml;base64,PHN2Zz4=',
    'data:image/png;base64,aGVsbG8=', 'custom:run']) {
    const html = renderMarkdown(`[打开](${url})\n\n![图片](${url})`);
    assert.doesNotMatch(html, /<(?:a|img)\b/i, url);
  }
});

test('relative anchors, safe images, mail and automatic links work', () => {
  const html = renderMarkdown('[章节](#重点) [页面](/docs/1) [邮件](mailto:test@example.com)\n\n![图](https://example.com/a.png)\n\nhttps://example.com');
  assert.match(html, /href="#%E9%87%8D%E7%82%B9"/);
  assert.match(html, /href="\/docs\/1"/);
  assert.match(html, /href="mailto:test@example\.com"/);
  assert.match(html, /<img src="https:\/\/example\.com\/a\.png" alt="图">/);
  assert.match(html, />https:\/\/example\.com<\/a>/);
});

test('every growing Markdown prefix is safe and incomplete fences show code', () => {
  const text = '# 流式回答\n\n**重点**\n\n```js\n<script>alert(1)</script>\n```\n\n[链接](javascript:alert(1))';
  for (const prefix of Array.from(text).map((_, index, chars) => chars.slice(0, index + 1).join(''))) {
    const html = renderMarkdown(prefix);
    assert.doesNotMatch(html, /<script\b|href="javascript:/i);
  }
  assert.match(renderMarkdown('```python\nprint("你好")'), /<pre><code class="language-python">print/);
  assert.equal(renderMarkdown(null), '');
  assert.equal(renderMarkdown(''), '');
});

test('the browser bundle exposes the same renderer without a CDN', () => {
  const context = vm.createContext({atob});
  vm.runInContext(fs.readFileSync(path.join(assetPath, 'vendor/markdown-it.umd.min.js'), 'utf8'), context);
  vm.runInContext(fs.readFileSync(path.join(assetPath, 'markdown.js'), 'utf8'), context);
  assert.equal(typeof context.InterviewMarkdown.renderMarkdown, 'function');
  assert.equal(context.InterviewMarkdown.renderMarkdown('**浏览器**'), '<p><strong>浏览器</strong></p>\n');
});

test('a missing parser still displays escaped text safely', () => {
  const context = vm.createContext({});
  vm.runInContext(fs.readFileSync(path.join(assetPath, 'markdown.js'), 'utf8'), context);
  const html = context.InterviewMarkdown.renderMarkdown('<script>alert(1)</script>\n下一行');
  assert.match(html, /&lt;script&gt;/);
  assert.match(html, /<br>\n下一行/);
  assert.doesNotMatch(html, /<script>/);
});
