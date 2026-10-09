const test = require('node:test');
const assert = require('node:assert/strict');
const {createAnswerStream} = require('../../src/interview_intelligence/web/assets/answer-stream.js');
const {watchQueryRun} = require('../../src/interview_intelligence/web/assets/query-stream.js');

function manualClock() {
  let sequence = 0;
  const tasks = new Map();
  return {
    schedule(callback, delay) {
      const id = ++sequence;
      tasks.set(id, {callback, delay});
      return id;
    },
    cancel(id) { tasks.delete(id); },
    tick() {
      const entry = tasks.entries().next().value;
      assert.ok(entry, 'expected a pending character render');
      tasks.delete(entry[0]);
      entry[1].callback();
    },
    drain() {
      for (let limit = 10000; tasks.size; limit--) {
        assert.ok(limit > 0, 'character queue did not finish');
        this.tick();
      }
    },
    get size() { return tasks.size; },
    get delay() { return tasks.values().next().value?.delay; }
  };
}

function fixture() {
  const clock = manualClock(), rendered = [];
  const stream = createAnswerStream({
    render: text => rendered.push(text),
    schedule: (callback, delay) => clock.schedule(callback, delay),
    cancel: id => clock.cancel(id),
    intervalMs: 16,
    charactersPerTick: 1
  });
  return {clock, rendered, stream};
}

test('a complete SSE chunk is revealed one Unicode code point per scheduled tick', () => {
  const {clock, rendered, stream} = fixture();
  stream.append('答😀案');
  assert.equal(stream.text, '');
  assert.deepEqual(rendered, []);
  assert.equal(clock.size, 1);
  assert.equal(clock.delay, 16);

  clock.tick();
  assert.equal(stream.text, '答');
  clock.tick();
  assert.equal(stream.text, '答😀');
  clock.tick();
  assert.equal(stream.text, '答😀案');
  assert.deepEqual(rendered, ['答', '答😀', '答😀案']);
  assert.equal(clock.size, 0);
});

test('later chunks preserve ordering and reuse the active paced queue', () => {
  const {clock, rendered, stream} = fixture();
  stream.append('第一');
  clock.tick();
  stream.append('步\n');
  stream.append('第二步');
  assert.equal(clock.size, 1);
  assert.deepEqual(rendered, ['第']);
  clock.drain();
  assert.equal(stream.text, '第一步\n第二步');
  assert.deepEqual(rendered, Array.from('第一步\n第二步', (_, index) =>
    Array.from('第一步\n第二步').slice(0, index + 1).join('')));
});

test('completion waits for the queued answer instead of replacing it all at once', async () => {
  const {clock, rendered, stream} = fixture();
  stream.append('答案');
  clock.tick();
  let finished = false;
  const completion = stream.finish('答案完整。').then(() => { finished = true; });
  await Promise.resolve();
  assert.equal(finished, false);
  assert.equal(stream.text, '答');
  stream.append('结束后的重复片段');
  clock.tick();
  assert.equal(stream.text, '答案');
  assert.equal(finished, false);
  clock.drain();
  await completion;
  assert.equal(stream.text, '答案完整。');
  assert.equal(finished, true);
  assert.equal(rendered.at(-1), '答案完整。');
  assert.equal(clock.size, 0);
});

test('a completion without prior deltas still reveals the fallback answer incrementally', async () => {
  const {clock, rendered, stream} = fixture();
  const completion = stream.finish('**参考回答**');
  assert.equal(stream.text, '');
  assert.deepEqual(rendered, []);
  clock.tick();
  assert.equal(stream.text, '*');
  clock.drain();
  await completion;
  assert.equal(stream.text, '**参考回答**');
});

test('final reconciliation discards an unconfirmed suffix and ends at the checked answer', async () => {
  const {clock, rendered, stream} = fixture();
  stream.append('正错误后缀');
  clock.tick();
  assert.equal(stream.text, '正');
  const completion = stream.finish('正确答案');
  clock.drain();
  await completion;
  assert.equal(stream.text, '正确答案');
  assert.equal(rendered.some(text => text.includes('错误')), false);
});

test('a canonical answer that disagrees with visible text replaces the provisional answer', async () => {
  const {clock, stream} = fixture();
  stream.append('草稿');
  clock.tick();
  assert.equal(stream.text, '草');
  const completion = stream.finish('最终答案');
  clock.drain();
  await completion;
  assert.equal(stream.text, '最终答案');
  assert.equal(clock.size, 0);
});

test('stable full-text snapshots replace a backlog without replaying already visible text', () => {
  const {clock, rendered, stream} = fixture();
  stream.setText('原始草稿');
  clock.tick();
  assert.equal(stream.text, '原');
  stream.setText('原始草稿');
  stream.setText('原始回答');
  assert.equal(clock.size, 1);
  assert.deepEqual(rendered, ['原']);
  clock.drain();
  assert.equal(stream.text, '原始回答');
  assert.deepEqual(rendered, ['原', '原始', '原始回', '原始回答']);
  stream.setText('原始回答');
  assert.equal(clock.size, 0);
});

test('a new model attempt resets both the visible draft and the old queued suffix', () => {
  const {clock, rendered, stream} = fixture();
  stream.append('旧草稿后缀');
  clock.tick();
  assert.equal(stream.text, '旧');
  stream.reset('新回答');
  assert.equal(stream.text, '');
  assert.equal(clock.size, 1);
  clock.tick();
  assert.equal(stream.text, '新');
  clock.drain();
  assert.equal(stream.text, '新回答');
  assert.deepEqual(rendered, ['旧', '', '新', '新回', '新回答']);
});

test('stop freezes visible text, cancels scheduled output, and ignores late chunks', async () => {
  const {clock, rendered, stream} = fixture();
  stream.append('已显示和待显示');
  clock.tick();
  const stopped = stream.stop();
  assert.equal(stopped, '已');
  assert.equal(clock.size, 0);
  stream.append('迟到片段');
  await stream.finish('迟到完成结果');
  assert.equal(stream.text, '已');
  assert.deepEqual(rendered, ['已']);
  assert.equal(clock.size, 0);
});

test('stopping during completion also releases the caller waiting for the character queue', async () => {
  const {clock, stream} = fixture();
  stream.append('答案');
  const completion = stream.finish('答案很长');
  clock.tick();
  stream.stop();
  await completion;
  assert.equal(stream.text, '答');
  assert.equal(clock.size, 0);
});

class Source {
  static latest;
  constructor(url) { this.url = String(url); this.listeners = {}; this.closed = false; Source.latest = this; }
  addEventListener(type, callback) { this.listeners[type] = callback; }
  emit(type, data, sequence) {
    this.listeners[type]?.({data: JSON.stringify(data), lastEventId: String(sequence)});
  }
  close() { this.closed = true; }
}

test('SSE journal replays do not duplicate answer characters or expose internal model text', async () => {
  const {clock, stream} = fixture();
  const deltas = [];
  const result = watchQueryRun({
    url: 'http://localhost/api/runs/r1/events',
    snapshot: {status: 'RUNNING', event_cursor: 0, last_sequence: 2, events: [
      {sequence: 1, type: 'accepted', data: {run_id: 'r1'}},
      {sequence: 2, type: 'answer_delta', data: {text: '第'}}
    ]},
    EventSourceClass: Source,
    onEvent: event => {
      if (event.type === 'answer_delta') {
        deltas.push(event.text);
        if (typeof event.text === 'string') stream.setText(event.text);
        else stream.append(event.delta);
      }
    },
    readRun: async () => { throw new Error('unexpected recovery'); }
  });
  const source = Source.latest;
  assert.match(source.url, /after=2/);
  source.emit('answer_delta', {text: '第'}, 2);
  source.emit('answer_delta', {text: '第一步', delta: '一步'}, 3);
  source.emit('answer_delta', {text: '第一步', delta: '一步'}, 3);
  source.emit('text_delta', {text: 'private planner text'}, 4);
  source.emit('completed', {result: {answer: '第一步。'}}, 5);
  const final = await result;
  const completion = stream.finish(final.answer);
  assert.deepEqual(deltas, ['第', '第一步']);
  assert.equal(stream.text, '');
  clock.tick();
  assert.equal(stream.text, '第');
  clock.drain();
  await completion;
  assert.equal(stream.text, '第一步。');
  assert.equal(source.closed, true);
});

test('recovery drains journal pages once and keeps rendering through the same answer queue', async () => {
  const {clock, stream} = fixture();
  const cursors = [];
  const result = watchQueryRun({
    url: 'http://localhost/api/runs/r1/events',
    snapshot: {status: 'RUNNING', event_cursor: 0, last_sequence: 1, events: [
      {sequence: 1, type: 'accepted', data: {run_id: 'r1'}}
    ]},
    EventSourceClass: Source,
    onEvent: event => {
      if (event.type === 'answer_delta') {
        if (typeof event.text === 'string') stream.setText(event.text);
        else stream.append(event.delta);
      }
    },
    readRun: async cursor => {
      cursors.push(cursor);
      if (cursor === 2) return {status: 'SUCCEEDED', last_sequence: 5, events: [
        {sequence: 2, type: 'answer_delta', data: {text: '第'}},
        {sequence: 3, type: 'answer_delta', data: {text: '第一步', delta: '一步'}}
      ]};
      assert.equal(cursor, 3);
      return {status: 'SUCCEEDED', last_sequence: 5, events: [
        {sequence: 3, type: 'answer_delta', data: {text: '第一步', delta: '一步'}},
        {sequence: 4, type: 'answer_delta', data: {text: '第一步。', delta: '。'}},
        {sequence: 5, type: 'completed', data: {result: {answer: '第一步。'}}}
      ]};
    }
  });
  Source.latest.emit('answer_delta', {text: '第'}, 2);
  clock.tick();
  Source.latest.onerror();
  const final = await result;
  const completion = stream.finish(final.answer);
  assert.equal(stream.text, '第');
  clock.drain();
  await completion;
  assert.deepEqual(cursors, [2, 3]);
  assert.equal(stream.text, '第一步。');
});
