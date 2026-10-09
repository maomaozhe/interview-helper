(function (root, factory) {
  "use strict";
  const isCommonJS = typeof module !== "undefined" && module.exports;
  const MarkdownIt = isCommonJS ? require("./vendor/markdown-it.umd.min.js") : root.markdownit;
  const api = factory(MarkdownIt);
  if (isCommonJS) module.exports = api;
  else root.InterviewMarkdown = api;
})(typeof window !== "undefined" ? window : globalThis, function (MarkdownIt) {
  "use strict";

  function escapeHTML(text) {
    return text.replace(/[&<>"']/g, character => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
    })[character]);
  }

  // Raw HTML is always text. Keep the parser's escaping and URL normalization,
  // and restrict links/images to web, email, and relative URLs.
  const parser = typeof MarkdownIt === "function" ? new MarkdownIt({
    html: false,
    breaks: true,
    linkify: true,
    typographer: false,
    maxNesting: 50
  }) : null;

  if (parser) {
    const defaultValidateLink = parser.validateLink.bind(parser);
    parser.validateLink = url => {
      if (!defaultValidateLink(url)) return false;
      const compact = String(url).trim().replace(/[\u0000-\u0020\u007f]/g, "");
      const scheme = compact.match(/^([a-z][a-z\d+.-]*):/i);
      return !scheme || /^(https?|mailto)$/i.test(scheme[1]);
    };
    parser.renderer.rules.link_open = (tokens, index, options, environment, renderer) => {
      const token = tokens[index];
      if (/^(?:https?:)?\/\//i.test(token.attrGet("href") || "")) {
        token.attrSet("target", "_blank");
        token.attrSet("rel", "noopener noreferrer");
      }
      return renderer.renderToken(tokens, index, options);
    };
  }

  function renderMarkdown(value) {
    const text = String(value ?? "");
    if (!text) return "";
    // An unavailable asset must still leave the answer readable and escaped.
    return parser ? parser.render(text) : `<p>${escapeHTML(text).replace(/\r?\n/g, "<br>\n")}</p>`;
  }

  return {renderMarkdown};
});
