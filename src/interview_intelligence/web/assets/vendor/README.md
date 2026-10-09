# markdown-it

`markdown-it.umd.min.js` and its source map are the unmodified browser build of
markdown-it **15.0.2**, distributed under the MIT license in `markdown-it.LICENSE`.
They are served locally; the browser does not load a CDN dependency.
Bundled dependency notices are preserved in `THIRD_PARTY_LICENSES.md`.

- Project: https://github.com/markdown-it/markdown-it
- Package: https://registry.npmjs.org/markdown-it/-/markdown-it-15.0.2.tgz
- Package integrity (SHA-512):
  `q4IGxMv56jCqT4OCRCADBoDP3LO4MhmTXjFbphHPXs4g3j9Xg5RDnxqN8IF/3vIWEU+VCnUq+7JUg/cfy2E6Qw==`
- Original bundle path: `package/dist/browser/markdown-it.umd.min.js`

To upgrade, verify the registry package integrity, then replace the browser UMD
bundle, matching source map, and license from the same release. Keep the secure
parser options in `../markdown.js` and run `node --test tests/web/test_markdown.cjs`.
