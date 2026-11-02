import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import ts from 'typescript';
const source = readFileSync(new URL('../../src/features/assistant/runtime/agent-events.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText;
const { takeSseFrames, parseAgentEvent, sseControl, addDelta, latestDelta } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);

test('SSE chunk boundaries retain complete multiline JSON frames and ignore heartbeat comments', () => {
  const event = { seq: 9, event_type: 'run.finished', run_id: '12', payload: {}, created_at: '2026-10-03' };
  const first = takeSseFrames(': heartbeat\r\n\r\nid: 9\r\ndata: {"seq":9,\r\ndata: "event_type":"run.finished",');
  assert.equal(parseAgentEvent(first.frames[0]), null);
  const next = takeSseFrames(first.rest + '\r\ndata: "run_id":"12","payload":{},"created_at":"2026-10-03"}\r\n\r\n');
  assert.deepEqual(parseAgentEvent(next.frames[0]), event);
  assert.equal(next.rest, '');
});

test('SSE control frames remain distinct from durable events and reject malformed payloads', () => {
  for (const kind of ['resync', 'retry', 'access-ended']) {
    const frame = `event: ${kind}\ndata: {"code":"protocol-control"}`;
    assert.equal(sseControl(frame), kind);
    assert.equal(parseAgentEvent(frame), null);
  }
  assert.equal(sseControl('event: retry\nevent: agent\ndata: {}'), null);
  assert.equal(parseAgentEvent('data: {"seq":1,"event_type":"step.started","payload":[]}'), null);
});
test('delta replay deduplicates turn and offset without comparing Unicode codepoint offsets with JS length', () => {
  const event = (turn_id, offset, delta) => ({ seq: 1, event_type: 'assistant.delta', run_id: '12', payload: { turn_id, offset, delta }, created_at: '' });
  let chunks = addDelta([], event('a', 0, '🎬'));
  chunks = addDelta(chunks, event('a', 1, '雨夜'));
  chunks = addDelta(chunks, event('a', 1, '雨夜'));
  assert.equal(latestDelta(chunks), '🎬雨夜');
  assert.equal(chunks.length, 2);
  chunks = addDelta(chunks, event('b', 0, '新回复'));
  assert.equal(latestDelta(chunks), '新回复');
});

test('trace SSE payloads retain decimal identities, byte offsets and execution scope across chunks', () => {
  const event = { seq: 12, run_id: '9007199254740993', event_type: 'trace.delta', created_at: '2026-10-30', payload: {
    block_id: '9007199254740995', block_version: '9007199254740997', execution_scope_id: '9007199254740999',
    parent_call_id: '9007199254741001', tool_call_id: '9007199254741003', subtask_id: '9007199254741005',
    source: 'assistant', channel: 'stderr', offset: 7, delta: '雨🙂', created_at: '2026-10-30',
  } };
  const frame = `id: 12\nevent: agent\ndata: ${JSON.stringify(event)}\n\n`;
  const first = takeSseFrames(frame.slice(0, 73));
  assert.deepEqual(first.frames, []);
  const complete = takeSseFrames(first.rest + frame.slice(73));
  assert.deepEqual(parseAgentEvent(complete.frames[0]), event);
  assert.equal(complete.rest, '');
});
