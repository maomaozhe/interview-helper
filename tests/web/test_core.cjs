const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const corePath = path.resolve(__dirname, '../../src/interview_intelligence/web/assets/core.js');
const core = fs.existsSync(corePath) ? require(corePath) : {};

test('question detail keeps only the filters supported by its API', () => {
  assert.equal(typeof core.pickFilters, 'function');
  assert.deepEqual(core.pickFilters({company:'字节',topic_l1:'Redis',round:'SECOND',
    group_by:'question',topic_level:'L2',sort:'frequency',limit:20,cursor:null}),
    {company:'字节',topic_l1:'Redis',round:'SECOND'});
});

test('detail filters preserve the date range, language and question type', () => {
  assert.equal(typeof core.pickFilters, 'function');
  const filters = {start_date:'2026-07-01',end_date:'2026-10-02',date_basis:'BEST_AVAILABLE',
    language:'JAVA',question_type:'SCENARIO',topic_l2:'JVM'};
  assert.deepEqual(core.pickFilters(filters), filters);
  assert.deepEqual(core.pickFilters(null), {});
});

test('an older health response cannot undo an already synchronized import', () => {
  const ready = {current_revision:57,indexed_revision:57,index:'ready'};
  const earlier = {current_revision:57,indexed_revision:56,index:'stale'};
  assert.deepEqual(core.selectHealthSnapshot(ready, earlier), ready);
  assert.deepEqual(core.selectHealthSnapshot(earlier, ready), ready);
  assert.deepEqual(core.selectHealthSnapshot(ready,
    {current_revision:56,indexed_revision:56,index:'ready'}), ready);
});

test('new corpus progress and a service failure at the same revision remain visible', () => {
  const ready = {current_revision:57,indexed_revision:57,index:'ready'};
  const importing = {current_revision:58,indexed_revision:57,index:'stale'};
  const unavailable = {...ready,index:'unavailable'};
  assert.deepEqual(core.selectHealthSnapshot(null, ready), ready);
  assert.deepEqual(core.selectHealthSnapshot(ready, importing), importing);
  assert.deepEqual(core.selectHealthSnapshot(ready, unavailable), unavailable);
});

test('a query on a newer corpus refreshes the workspace opened before import progressed', () => {
  const opened = {current_revision:71,indexed_revision:71,index:'ready'};
  assert.equal(core.shouldRefreshWorkspace(opened,{corpus_revision:75}),true);
  assert.equal(core.shouldRefreshWorkspace(opened,{corpus_revision:71}),false);
  assert.equal(core.shouldRefreshWorkspace({current_revision:76,indexed_revision:76,index:'ready'},
    {corpus_revision:75}),false);
});

test('queries recheck a lagging index without using absent result versions as progress', () => {
  assert.equal(core.shouldRefreshWorkspace({current_revision:75,indexed_revision:74,index:'stale'},
    {corpus_revision:75}),true);
  assert.equal(core.shouldRefreshWorkspace(null,{corpus_revision:75}),true);
  assert.equal(core.shouldRefreshWorkspace({current_revision:75,indexed_revision:75,index:'ready'},{}),false);
});

test('published document filters keep existing versions while updates wait or fail', () => {
  const documents = [
    {path:'published.md',status:'QUEUED',active_status:'INCLUDED'},
    {path:'failed-update.md',status:'FAILED',active_status:'INCLUDED'},
    {path:'excluded.md',status:'QUEUED',active_status:'EXCLUDED'},
    {path:'new.md',status:'QUEUED',active_status:null}
  ];
  const paths = status => documents.filter(item => core.matchesDocumentStatus(item,status)).map(item => item.path);
  assert.deepEqual(paths('INCLUDED'),['published.md','failed-update.md']);
  assert.deepEqual(paths('EXCLUDED'),['excluded.md']);
  assert.deepEqual(paths('QUEUED'),['published.md','excluded.md','new.md']);
  assert.deepEqual(paths('FAILED'),['failed-update.md']);
});

test('not yet published includes new queued running and failed sources', () => {
  const documents = ['PENDING','QUEUED','RUNNING','FAILED','NEEDS_REVIEW'].map(status => ({status,active_status:null}));
  assert.equal(documents.filter(item => core.matchesDocumentStatus(item,'PENDING')).length,5);
  assert.equal(core.matchesDocumentStatus({status:'RUNNING',active_status:'INCLUDED'},'PENDING'),false);
  assert.equal(core.matchesDocumentStatus({status:'QUEUED',active_status:'EXCLUDED'},'PENDING'),false);
});

test('changed content stays findable after a reimport enters the queue', () => {
  const queued = {status:'QUEUED',active_status:'INCLUDED',content_changed:true};
  assert.equal(core.matchesDocumentStatus(queued,'CHANGED'),true);
  assert.equal(core.matchesDocumentStatus({...queued,content_changed:false},'CHANGED'),false);
  assert.equal(core.matchesDocumentStatus(queued,''),true);
});
