const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const app = fs.readFileSync(path.resolve(__dirname, '../../src/interview_intelligence/web/assets/app.js'), 'utf8');
const apiSource = app.slice(app.indexOf('async function api('), app.indexOf('\nfunction filters()'));

async function requestParams(params) {
  let requested;
  const context = vm.createContext({URL, performance, window: {},
    document: {baseURI: 'http://localhost:8011/'},
    fetch: async url => {
      requested = url;
      return {ok: true, json: async () => ({data: [], meta: {}})};
    }});
  vm.runInContext(apiSource, context);
  await context.api('api/questions/list', {params});
  return requested.searchParams;
}

test('opening an unfiltered library omits an empty review status list', async () => {
  const params = await requestParams({review_statuses: [], cursor: null, page_size: 20});
  assert.equal(params.has('review_statuses'), false);
  assert.equal(params.has('cursor'), false);
  assert.equal(params.get('page_size'), '20');
});

test('multiple review statuses are separate query parameters accepted by FastAPI', async () => {
  const params = await requestParams({review_statuses: ['UNSEEN', 'WEAK'], company: '字节'});
  assert.deepEqual(params.getAll('review_statuses'), ['UNSEEN', 'WEAK']);
  assert.equal(params.get('company'), '字节');
});
