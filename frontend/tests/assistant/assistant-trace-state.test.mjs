import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import ts from 'typescript';

const url = source => `data:text/javascript;base64,${Buffer.from(ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText).toString('base64')}`;
const traceUrl = url(readFileSync(new URL('../../src/features/assistant/runtime/assistant-trace.ts', import.meta.url), 'utf8'));
const trace = await import(traceUrl);
const presentation = await import(url(readFileSync(new URL('../../src/features/assistant/messages/trace-presentation.ts', import.meta.url), 'utf8')));
const timeline = await import(url(readFileSync(new URL('../../src/features/assistant/messages/agent-timeline.ts', import.meta.url), 'utf8')));
const projection = await import(url(readFileSync(new URL('../../src/features/assistant/runtime/assistant-task-state.ts', import.meta.url), 'utf8').replace('@host/features/assistant/runtime/assistant-trace', traceUrl)));
const block = (patch = {}) => ({ block_id: 'tool:201', block_version: '1', run_id: '101', execution_scope_id: '301', parent_call_id: null, subtask_id: null, tool_call_id: '201', kind: 'sandbox', source: 'assistant', visibility: 'step', status: 'running', started_seq: 1, started_at: '', updated_at: '', finished_at: null, action: 'execute', title: '执行命令', target: { kind: 'command', id: '201', display_name: 'python' }, channels: {}, resources: [], result_refs: [], ...patch });
const delta = (offset, text, channel = 'stdout', patch = {}) => ({ seq: 2, run_id: '101', event_type: 'trace.delta', created_at: '', payload: { block_id: 'tool:201', block_version: '2', execution_scope_id: '301', channel, offset, delta: text, ...patch } });

test('trace uses byte offsets and keeps each block and channel independent under replay', () => {
  let state = trace.mergeTraceBlocks({ traceBlocks: {}, traceDeltas: {} }, [block(), block({ block_id: 'tool:202', execution_scope_id: '302', parent_call_id: '201' })]);
  for (const event of [delta(7, 'b'), delta(0, '雨🙂'), delta(0, '雨🙂'), delta(0, '错误', 'stderr'), delta(0, '子任务', 'stdout', { block_id: 'tool:202', execution_scope_id: '302' })]) state = trace.applyTraceEvent(state, event);
  assert.equal(state.traceBlocks['tool:201'].channels.stdout.preview, '雨🙂b');
  assert.equal(state.traceBlocks['tool:201'].channels.stderr.preview, '错误');
  assert.equal(state.traceBlocks['tool:202'].channels.stdout.preview, '子任务');
  assert.deepEqual(state.traceDeltas['tool:201'], []);
});

test('a truncated UTF8 preview cannot fill a missing partial character with later ASCII', () => {
  let state = trace.mergeTraceBlocks({ traceBlocks: {}, traceDeltas: {} }, [block()]);
  state = trace.applyTraceEvent(state, delta(0, 'a'.repeat(16383)));
  state = trace.applyTraceEvent(state, delta(16383, '🙂'));
  state = trace.applyTraceEvent(state, delta(16387, 'b'));
  assert.equal(state.traceBlocks['tool:201'].channels.stdout.preview, 'a'.repeat(16383));
  assert.equal(state.traceBlocks['tool:201'].channels.stdout.total_bytes, 16388);
});

test('pending trace gaps stay bounded while the archive remains the full-content source', () => {
  let state = trace.mergeTraceBlocks({ traceBlocks: {}, traceDeltas: {} }, [block()]);
  for (let i = 1; i < 400; i++) state = trace.applyTraceEvent(state, delta(i * 2, '雨'.repeat(100)));
  const pending = state.traceDeltas['tool:201'];
  assert.ok(pending.length <= 128);
  assert.ok(pending.reduce((size, item) => size + new TextEncoder().encode(item.delta).length, 0) <= 16384);
});

test('waiting input survives snapshot and step event selection for stop controls', () => {
  const waiting = { id: '501', row_version: '2', status: 'waiting_input' };
  assert.equal(projection.selectedTask({ '501': waiting }), waiting);
  let state = projection.emptyTaskProjection('3');
  state = projection.applyTaskEvent(state, { seq: 1, run_id: '101', event_type: 'step.waiting_input', created_at: '', payload: { operation_id: '201', task_id: '501', call_id: '601', tool_name: 'runtime__ask_user', trace_version: '2', phase: 'waiting_input' } });
  assert.equal(state.operations['201']?.outcome, 'waiting_input');
});

test('child scopes remain inside their own run and different frozen Skill targets stay separate', () => {
  const root = block({ block_id: 'delegate', tool_call_id: '201', kind: 'subtask' });
  const own = block({ block_id: 'child', parent_call_id: '201', execution_scope_id: '302' });
  const other = block({ block_id: 'other-run', run_id: '102', parent_call_id: '201', execution_scope_id: '303' });
  const firstSkill = block({ block_id: 'skill-1', tool_name: 'read_skill_resource', title: '读取创作方法', target: { kind: 'skill', id: '11', version: '7', display_name: '节奏方法', path: 'references/节奏.md' } });
  const secondSkill = block({ ...firstSkill, block_id: 'skill-2', target: { kind: 'skill', id: '12', version: '9', display_name: '对白方法', path: 'scripts/dialogue.py' } });
  const blocks = [root, own, other, firstSkill, secondSkill];
  assert.deepEqual(presentation.traceChildren(root, blocks), [own]);
  assert.deepEqual(presentation.traceRoots(blocks), [root, firstSkill, secondSkill]);
  const entries = timeline.buildTimeline([], [], blocks);
  assert.deepEqual(entries.map(item => item.block.block_id), ['delegate', 'skill-1', 'skill-2']);
  assert.deepEqual(entries.slice(1).map(item => item.block.target), [firstSkill.target, secondSkill.target]);
});

test('tool labels keep the action and concrete target without presentation versions or paths', () => {
  const item = block({ action: '执行创作操作', title: '执行创作操作《旧标题》 · v99 / secret.json', target: { kind: 'project', id: '1', display_name: '第一个项目', version: '99', path: 'secret.json' } });
  assert.equal(presentation.traceTitle(item), '执行创作操作《第一个项目》');
  assert.equal(presentation.traceTitle({ ...item, action: '查看 Skill', target: { ...item.target, display_name: '短剧分镜' } }), '查看 Skill《短剧分镜》');
  assert.equal(presentation.traceStatus('failed'), '失败');
});

test('historical runs without trace blocks retain messages, null-target operations and artifacts', () => {
  const messages = [{ id: '1', role: 'user', seq: 1, content: '历史要求' }, { id: '2', role: 'assistant', seq: 2, content: '历史答复' }];
  const ordinary = { id: '201', run_id: '101', visibility: 'silent', label: '历史工具', target: null, outcome: 'succeeded' };
  const artifact = { ...ordinary, id: '202', visibility: 'artifact', label: '历史成果', result_refs: [{ kind: 'asset', id: '11' }] };
  const entries = timeline.buildTimeline(messages, [ordinary, artifact], []);
  assert.deepEqual(entries.filter(item => item.kind === 'message').map(item => item.message.content), ['历史要求', '历史答复']);
  assert.deepEqual(entries.find(item => item.kind === 'steps').operations, [ordinary]);
  assert.equal(entries.find(item => item.kind === 'artifact').operation, artifact);
});
