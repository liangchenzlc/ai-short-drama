import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import ts from 'typescript';

const url = source => `data:text/javascript;base64,${Buffer.from(ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText).toString('base64')}`;
const traceUrl = url(readFileSync(new URL('../../src/features/assistant/runtime/assistant-trace.ts', import.meta.url), 'utf8'));
const traceTypesUrl = url(readFileSync(new URL('../../src/types/assistant/trace.ts', import.meta.url), 'utf8'));
const trace = await import(traceUrl);
const presentation = await import(url(readFileSync(new URL('../../src/features/assistant/messages/trace-presentation.ts', import.meta.url), 'utf8')));
const timelineCode = readFileSync(new URL('../../src/features/assistant/messages/agent-timeline.ts', import.meta.url), 'utf8').replace('@host/types/assistant/trace', traceTypesUrl);
const timeline = await import(url(timelineCode));
const projectionCode = readFileSync(new URL('../../src/features/assistant/runtime/assistant-task-state.ts', import.meta.url), 'utf8')
  .replace('@host/features/assistant/runtime/assistant-trace', traceUrl)
  .replace('@host/types/assistant/trace', traceTypesUrl);
const projection = await import(url(projectionCode));
const block = (patch = {}) => ({ block_id: 'tool:201', block_version: '1', run_id: '101', execution_scope_id: '301', parent_call_id: null, subtask_id: null, tool_call_id: '201', kind: 'sandbox', source: 'assistant', trace_visibility: 'visible', status: 'running', started_seq: 1, updated_seq: 1, result_seq: null, started_at: '', updated_at: '', finished_at: null, action: 'execute', title: '执行命令', target: { kind: 'command', id: '201', display_name: 'python' }, channels: {}, resources: [], result_refs: [], ...patch });

test('persisted narration suppresses only its own streamed turn', () => {
  const narration = { id: '2', role: 'assistant', run_id: '101', event_seq: 3, content: '先检查', text_parts: [{ turn_id: 'a', event_seq: 2, content: '先检查' }] };
  const entries = timeline.buildTimeline([narration], [], [], [], [{ turn: 'a', seq: 2, text: '先检查' }, { turn: 'b', seq: 10, text: '新答复' }], '101');
  assert.deepEqual(entries[0].parts.map(part => part.text), ['先检查', '新答复']);
});

test('operation history only requests missing pages while snapshots refresh cached active states', () => {
  const snapshot = { conversation_id: '3', snapshot_cursor: 5, tasks: [{ id: '501', row_version: '1', active_operation: { id: '250', task_id: '501', state_version: '2', outcome: 'succeeded', result_refs: [], undo_receipts: [{ id: '1' }] } }], operations: [{ id: '200', task_id: '501' }], has_more_operations: true };
  assert.deepEqual(projection.operationPageStart(snapshot, '501'), { afterId: '200', complete: false });
  const completed = { afterId: '250', complete: true };
  assert.equal(projection.operationPageStart(snapshot, '501', completed), null);
  assert.deepEqual(projection.operationPageStart({ ...snapshot, tasks: [{ id: '501', active_operation: { id: '251' } }] }, '501', completed), { afterId: '250', complete: false });
  const state = projection.mergeTaskSnapshot({ ...projection.emptyTaskProjection('3'), operations: { '250': { ...snapshot.tasks[0].active_operation, state_version: '1', outcome: 'running', undo_receipts: [] } } }, snapshot);
  assert.equal(state.operations['250'].outcome, 'succeeded');
  assert.equal(state.operations['250'].undo_receipts[0].id, '1');
});

test('tail operations keep live terminal and undo updates beyond the snapshot prefix', () => {
  let state = { ...projection.emptyTaskProjection('3'), operations: { '250': { id: '250', task_id: '501', run_id: '101', call_id: '601', tool_name: 'workbench__apply_standard_patch', state_version: '1', outcome: 'running', undo_receipts: [], result_refs: [] } } };
  const snapshot = { conversation_id: '3', snapshot_cursor: 10, tasks: [{ id: '501', row_version: '2', active_operation: null }], operations: [{ id: '200', task_id: '501', state_version: '1', outcome: 'succeeded' }], has_more_operations: true, trace_blocks: [] };
  state = projection.mergeTaskSnapshot(state, snapshot);
  assert.equal(projection.operationPageStart(snapshot, '501', { afterId: '250', complete: true }), null);
  state = projection.applyTaskEvent(state, { seq: 9, run_id: '101', event_type: 'step.finished', created_at: '', payload: { operation_id: '250', task_id: '501', call_id: '601', tool_name: 'workbench__apply_standard_patch', trace_version: '2', phase: 'terminal', outcome: 'succeeded', result_refs: [{ kind: 'asset', id: '11' }] } });
  assert.equal(state.operations['250'].outcome, 'succeeded');
  const receipt = { id: '900', operation_id: '250', selected_keys: ['asset:11'], result_refs: [{ kind: 'asset', id: '11', status: 'deleted' }] };
  const undo = { seq: 10, run_id: '101', event_type: 'operation.undo.completed', payload: { operation_id: '250', receipt } };
  state = projection.applyTaskEvent(state, undo);
  state = projection.applyTaskEvent(state, undo);
  assert.deepEqual(state.operations['250'].undo_receipts, [receipt]);
  state = projection.applyTaskEvent(state, { seq: 11, run_id: '101', event_type: 'step.finished', payload: { operation_id: '250', task_id: '501', call_id: '601', tool_name: 'workbench__apply_standard_patch', trace_version: '1', outcome: 'failed' } });
  state = projection.applyTaskEvent(state, { seq: 12, run_id: '101', event_type: 'step.started', payload: { operation_id: '250', task_id: '501', call_id: '601', tool_name: 'workbench__apply_standard_patch', trace_version: '3' } });
  assert.equal(state.operations['250'].state_version, '2');
  assert.equal(state.operations['250'].outcome, 'succeeded');
  assert.deepEqual(state.operations['250'].result_refs, [{ kind: 'asset', id: '11' }]);
  assert.deepEqual(state.operations['250'].undo_receipts, [receipt]);
});

test('prepending earlier users preserves each split run segment and trace identity', () => {
  const goal = { id: 'goal', role: 'user', event_seq: 3, content: '历史内容' };
  const answer = { id: 'answer', role: 'user', event_seq: 8, content: '历史内容' };
  const narration = { id: 'narration', role: 'assistant', run_id: '101', event_seq: 4, content: '历史内容' };
  const reply = { id: 'reply', role: 'assistant', run_id: '101', event_seq: 10, content: '历史内容' };
  const root = block({ block_id: 'delegate', tool_call_id: '201', kind: 'subtask', started_seq: 5 });
  const child = block({ block_id: 'child', parent_call_id: '201', execution_scope_id: '302', started_seq: 6 });
  const messages = [goal, narration, answer, reply];
  const before = timeline.buildTimeline(messages, [], [root, child]);
  const after = timeline.buildTimeline([{ id: 'older', role: 'user', event_seq: 1, content: '历史内容' }, ...messages], [], [root, child]);
  const turns = entries => entries.filter(entry => entry.kind === 'assistant_turn');
  assert.equal(turns(before).length, 2);
  assert.deepEqual(turns(after).map(entry => entry.key), turns(before).map(entry => entry.key));
  assert.equal(new Set(after.map(entry => entry.key)).size, after.length);
  assert.equal(turns(after)[0].parts.find(part => part.kind === 'activity').block, root);
  assert.deepEqual(presentation.traceChildren(root, [root, child]), [child]);
});

test('loading the missing preceding user preserves split-run parts and trace scope identity', () => {
  const earlierUser = { id: 'earlier-user', role: 'user', event_seq: 3, content: '目标' };
  const reply = { id: 'reply', role: 'assistant', run_id: '101', event_seq: 4, content: '答复' };
  const laterUser = { id: 'later-user', role: 'user', event_seq: 8, content: '补充' };
  const laterReply = { id: 'later-reply', role: 'assistant', run_id: '101', event_seq: 10, content: '后续答复' };
  const root = block({ block_id: 'sandbox', kind: 'sandbox', started_seq: 5, status: 'succeeded' });
  const child = block({ block_id: 'child', parent_call_id: '201', execution_scope_id: '302', started_seq: 6 });
  const messages = [reply, laterUser, laterReply];
  const before = timeline.buildTimeline(messages, [], [root, child]);
  const after = timeline.buildTimeline([earlierUser, ...messages], [], [root, child]);
  const turns = entries => entries.filter(entry => entry.kind === 'assistant_turn');
  assert.equal(turns(before).length, 2);
  assert.equal(turns(after).length, 2);
  assert.deepEqual(turns(after).map(entry => entry.parts), turns(before).map(entry => entry.parts));
  assert.equal(new Set(after.map(entry => entry.key)).size, after.length);
  assert.equal(turns(after)[0].parts.find(part => part.kind === 'activity').block, root);
  assert.deepEqual(presentation.traceChildren(root, [root, child]), [child]);
  assert.deepEqual(after.filter(entry => entry.kind === 'message').map(entry => entry.message.id), ['earlier-user', 'later-user']);
});

test('an omitted trace block still accepts late UTF8 deltas below the global snapshot watermark', () => {
  const original = block();
  let state = projection.mergeTaskSnapshot(projection.emptyTaskProjection('3'), { conversation_id: '3', snapshot_cursor: 5, tasks: [], operations: [], trace_blocks: [original] });
  state = projection.mergeTaskSnapshot(state, { conversation_id: '3', snapshot_cursor: 10, tasks: [], operations: [], trace_blocks: [block({ block_id: 'tool:202' })] });
  const event = { ...delta(0, '雨🙂'), seq: 9 };
  state = projection.applyTaskEvent(state, event);
  state = projection.applyTaskEvent(state, event);
  assert.equal(state.traceBlocks[original.block_id].channels.stdout?.preview, '雨🙂');
  assert.equal(state.traceBlocks[original.block_id].channels.stdout?.total_bytes, 7);
  assert.equal(state.traceBlocks[original.block_id].block_version, '2');
  assert.deepEqual(state.traceDeltas[original.block_id], []);
});

test('an omitted trace block accepts a late terminal without allowing version or terminal rewind', () => {
  const original = block();
  const settled = block({ block_version: '3', status: 'succeeded', updated_seq: 9, result_seq: 9 });
  let state = projection.mergeTaskSnapshot(projection.emptyTaskProjection('3'), { conversation_id: '3', snapshot_cursor: 5, tasks: [], operations: [], trace_blocks: [original] });
  state = projection.mergeTaskSnapshot(state, { conversation_id: '3', snapshot_cursor: 10, tasks: [], operations: [], trace_blocks: [] });
  state = projection.applyTaskEvent(state, { seq: 9, run_id: '101', event_type: 'trace.block', payload: settled });
  assert.deepEqual(state.traceBlocks[original.block_id], settled);
  for (const stale of [block({ block_version: '2', status: 'failed' }), block({ block_version: '4' })]) {
    state = projection.applyTaskEvent(state, { seq: 11, run_id: '101', event_type: 'trace.block', payload: stale });
    assert.deepEqual(state.traceBlocks[original.block_id], settled);
  }
  state = projection.applyTaskEvent(state, { ...delta(0, '迟到', 'stdout', { block_version: '5' }), seq: 12 });
  assert.deepEqual(state.traceBlocks[original.block_id], settled);
  const deleted = projection.applyTaskEvent(state, { seq: 13, event_type: 'conversation.deleted', payload: {} });
  assert.deepEqual(deleted.traceBlocks, {});
  assert.deepEqual(deleted.traceDeltas, {});
  assert.equal(projection.applyTaskEvent(deleted, { seq: 14, run_id: '101', event_type: 'trace.block', payload: settled }), deleted);
});

test('snapshot-covered entities keep their replay fence even when an event claims a higher version', () => {
  const operation = { id: '250', task_id: '501', state_version: '2', outcome: 'running', result_refs: [], undo_receipts: [] };
  const original = block();
  const state = projection.mergeTaskSnapshot(projection.emptyTaskProjection('3'), { conversation_id: '3', snapshot_cursor: 10, tasks: [], operations: [operation], trace_blocks: [original] });
  const events = [
    { seq: 9, run_id: '101', event_type: 'step.finished', payload: { operation_id: '250', task_id: '501', call_id: '601', tool_name: 'read_task_results', trace_version: '9', outcome: 'succeeded' } },
    { seq: 9, run_id: '101', event_type: 'trace.block', payload: block({ block_version: '9', status: 'succeeded' }) },
    { ...delta(0, '旧回放', 'stdout', { block_version: '9' }), seq: 9 },
  ];
  for (const event of events) assert.equal(projection.applyTaskEvent(state, event), state);
});

test('active operations and entity history pages retain their own highest replay fence', () => {
  const operation = { id: '250', task_id: '501', run_id: '101', call_id: '601', tool_name: 'read_task_results', state_version: '1', outcome: 'running', result_refs: [], undo_receipts: [] };
  const original = block();
  let state = projection.mergeTaskSnapshot(projection.emptyTaskProjection('3'), { conversation_id: '3', snapshot_cursor: 20, tasks: [{ id: '501', row_version: '1', active_operation: operation }], operations: [], trace_blocks: [original] });
  state = projection.mergeTaskEntities(state, { snapshot_cursor: 18, operations: [operation, { ...operation, id: '251' }], trace_blocks: [original, block({ block_id: 'tool:202' })] });
  const finished = (id, seq) => ({ seq, run_id: '101', event_type: 'step.finished', payload: { operation_id: id, task_id: '501', call_id: '601', tool_name: 'read_task_results', trace_version: '2', outcome: 'succeeded' } });
  assert.equal(projection.applyTaskEvent(state, finished('250', 19)), state);
  assert.equal(projection.applyTaskEvent(state, { seq: 19, run_id: '101', event_type: 'trace.block', payload: block({ block_version: '2', status: 'succeeded' }) }), state);
  assert.equal(projection.applyTaskEvent(state, finished('251', 18)), state);
  assert.equal(projection.applyTaskEvent(state, { ...delta(0, '旧', 'stdout', { block_id: 'tool:202' }), seq: 18 }), state);
  state = projection.applyTaskEvent(state, finished('251', 19));
  state = projection.applyTaskEvent(state, { ...delta(0, '雨🙂', 'stdout', { block_id: 'tool:202' }), seq: 19 });
  assert.equal(state.snapshotCursor, 20);
  assert.equal(state.operations['251'].outcome, 'succeeded');
  assert.equal(state.traceBlocks['tool:202'].channels.stdout.preview, '雨🙂');
  assert.equal(state.traceBlocks['tool:202'].channels.stdout.total_bytes, 7);
  const deleted = projection.applyTaskEvent(state, { seq: 21, event_type: 'conversation.deleted', payload: {} });
  assert.equal(projection.mergeTaskEntities(deleted, { snapshot_cursor: 22, operations: [operation], trace_blocks: [original] }), deleted);
});

test('user answers split a run without moving later replies ahead of the user', () => {
  const goal = { id: '1', role: 'user', event_seq: 1, content: '目标' };
  const narration = { id: '2', role: 'assistant', run_id: '101', event_seq: 2, content: '提问' };
  const answer = { id: '3', role: 'user', event_seq: 8, content: '答案' };
  const reply = { id: '4', role: 'assistant', run_id: '101', event_seq: 10, content: '答复', text_parts: [{ turn_id: 'b', event_seq: 10, content: '答复' }] };
  const flatten = entries => entries.flatMap(entry => entry.kind === 'message' ? [entry.message.content] : entry.parts.filter(part => part.kind === 'text').map(part => part.text));
  const incremental = timeline.buildTimeline([goal, narration, answer], [], [], [], [{ turn: 'b', seq: 10, text: '答复' }], '101');
  const refreshed = timeline.buildTimeline([goal, narration, answer, reply], [], []);
  assert.deepEqual(flatten(incremental), ['目标', '提问', '答案', '答复']);
  assert.deepEqual(flatten(refreshed), flatten(incremental));
  assert.equal(new Set(refreshed.map(entry => entry.key)).size, refreshed.length);
});
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
  const parts = entries.find(item => item.kind === 'assistant_turn').parts;
  assert.deepEqual(parts.map(item => item.block.block_id), ['delegate', 'skill-1', 'skill-2']);
  assert.deepEqual(parts.slice(1).map(item => item.block.target), [firstSkill.target, secondSkill.target]);
});

test('tool labels keep the action and concrete target without presentation versions or paths', () => {
  const item = block({ action: '执行创作操作', title: '执行创作操作《旧标题》 · v99 / secret.json', target: { kind: 'project', id: '1', display_name: '第一个项目', version: '99', path: 'secret.json' } });
  assert.equal(presentation.traceTitle(item), '执行创作操作《第一个项目》');
  assert.equal(presentation.traceTitle({ ...item, action: '查看 Skill', target: { ...item.target, display_name: '短剧分镜' } }), '查看 Skill《短剧分镜》');
  assert.equal(presentation.traceStatus('failed'), '失败');
});

test('hidden activities stay out of the process while their business results remain visible', () => {
  const messages = [
    { id: '1', role: 'user', seq: 1, event_seq: 1, content: '历史要求' },
    { id: '2', role: 'assistant', seq: 2, run_id: '101', event_seq: 9, content: '历史答复' },
  ];
  const ordinary = { id: '201', run_id: '101', event_seq: 3, result_seq: 8, trace_visibility: 'hidden', label: '内部记忆', target: null, outcome: 'succeeded', result_refs: [{ kind: 'asset', id: '11' }] };
  const visible = { ...ordinary, id: '202', event_seq: 5, trace_visibility: 'visible', label: '读取作品', result_seq: 7, result_refs: [] };
  const entries = timeline.buildTimeline(messages, [ordinary, visible], []);
  assert.equal(entries[0].message.content, '历史要求');
  const turn = entries.find(item => item.kind === 'assistant_turn');
  assert.deepEqual(turn.parts.filter(item => item.kind === 'activity').map(item => item.operation?.id), ['202']);
  assert.deepEqual(turn.results.map(item => item.id), ['201']);
  assert.equal(turn.parts.find(item => item.operation)?.seq, 5);
  assert.equal(turn.results[0].result_seq, 8);
});

test('assistant turn parts preserve streamed text, tool start and final message event order', () => {
  const activity = block({ run_id: '101', block_id: 'operation:201', operation_id: '201', kind: 'tool', started_seq: 4, status: 'succeeded' });
  const before = { turn: '501', seq: 2, text: '先检查。' };
  const after = { turn: '502', seq: 6, text: '已完成。' };
  const finalMessage = {
    id: '2', role: 'assistant', run_id: '101', event_seq: 8, seq: 2, content: '最终答复',
    text_parts: [{ turn_id: '502', event_seq: 6, content: '最终答复' }],
  };
  const entries = timeline.buildTimeline([], [], [activity], [{ id: '101', status: 'succeeded' }], [before, after], '101');
  const turn = entries[0];
  assert.deepEqual(turn.parts.map(item => item.seq), [2, 4, 6]);
  const completed = timeline.buildTimeline([finalMessage], [], [activity], [{ id: '101', status: 'succeeded' }]);
  assert.deepEqual(completed[0].parts.map(item => item.seq), [4, 6]);
});
