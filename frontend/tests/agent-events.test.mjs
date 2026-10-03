import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import ts from 'typescript';
const source = readFileSync(new URL('../src/features/agents/agent-events.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText;
const { takeSseFrames, parseAgentEvent, addDelta, latestDelta, reviewParameterLabels, reviewTargetLabel } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);

test('SSE chunk boundaries retain complete multiline JSON frames and ignore heartbeat comments', () => {
  const event = { seq: 9, event_type: 'run.finished', run_id: '12', payload: {}, created_at: '2026-10-03' };
  const first = takeSseFrames(': heartbeat\r\n\r\nid: 9\r\ndata: {"seq":9,\r\ndata: "event_type":"run.finished",');
  assert.equal(parseAgentEvent(first.frames[0]), null);
  const next = takeSseFrames(first.rest + '\r\ndata: "run_id":"12","payload":{},"created_at":"2026-10-03"}\r\n\r\n');
  assert.deepEqual(parseAgentEvent(next.frames[0]), event);
  assert.equal(next.rest, '');
});
test('review settings use product labels and exclude private or unrecognized parameter fields', () => {
  assert.deepEqual(reviewParameterLabels({ resolution: '720p', aspect: '16:9', duration_ms: 5000, layout: 'grid4', reference_media_ids: ['1'], secret: 'private JSON', system_prompt: 'private instructions' }), ['清晰度 720p', '画幅 16:9', '时长 5 秒', '布局 四宫格', '参考图片 1 张']);
  assert.deepEqual(reviewParameterLabels({ resolution: 'private data', duration_ms: NaN, secret: 'private JSON' }), []);
  assert.equal(reviewTargetLabel({ kind: 'image', target_kind: 'shot', target_id: '41', target_label: '第 2 镜头 · 雨夜站台' }), '第 2 镜头 · 雨夜站台');
  assert.equal(reviewTargetLabel({ kind: 'image', target_kind: 'shot', target_id: '41' }), '选定镜头');
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
