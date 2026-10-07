import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import ts from 'typescript';

const source = readFileSync(new URL('../src/features/projects/assistant-navigation.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText;
const { assistantView, setAssistantOpen, selectAssistantConversation, selectEpisodeObject, replaceEpisodeView } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);

test('legacy mode links open historical records and remove object conversation scopes', () => {
  const view = assistantView('?mode=agent&conversation=9007199254740993&conversation_source=301&conversation_stage=source&agent_subject_assets=501&filter=待确认');
  assert.equal(view.get('assistant'), 'open');
  assert.equal(view.get('legacy_conversation'), '9007199254740993');
  assert.equal(view.has('conversation'), false);
  assert.equal(view.get('filter'), '待确认');
  assert.equal([...view.keys()].some(key => key === 'mode' || key.startsWith('conversation_') || key.startsWith('agent_subject_')), false);
});

test('legacy mode without a record opens the project assistant, ordinary links stay closed', () => {
  assert.equal(assistantView('?mode=agent').get('assistant'), 'open');
  for (const search of ['', '?mode=prompt', '?mode=unknown']) {
    assert.equal(assistantView(search).has('mode'), false);
    assert.equal(assistantView(search).has('assistant'), false);
  }
});

test('opening and closing preserve one project conversation and historical selection', () => {
  const view = setAssistantOpen('?conversation=9007199254740993&filter=保留', true);
  const closed = setAssistantOpen(view.toString(), false);
  assert.equal(closed.get('conversation'), '9007199254740993');
  assert.equal(closed.get('filter'), '保留');
  assert.equal(closed.has('assistant'), false);
  assert.equal(setAssistantOpen('?legacy_conversation=301', false).get('legacy_conversation'), '301');
});

test('selecting a new chat leaves historical record without reviving old modes', () => {
  const selected = selectAssistantConversation('?assistant=open&legacy_conversation=301&conversation_assets=302', '9007199254740997');
  assert.equal(selected.get('conversation'), '9007199254740997');
  assert.equal(selected.has('legacy_conversation'), false);
  assert.equal(selected.get('assistant'), 'open');
  assert.equal(selected.has('mode'), false);
});

test('legacy editing links preserve stable object selection without creating a chat', () => {
  const view = assistantView('?agent_subject_storyboard=9007199254740997&agent_subject_assets=501');
  assert.equal(view.get('shot'), '9007199254740997');
  assert.equal(view.get('asset'), '501');
  assert.equal(view.has('assistant'), false);
  assert.equal(view.has('conversation'), false);
  assert.equal(view.has('agent_subject_storyboard'), false);
  assert.equal(assistantView('?shot=invalid&agent_subject_assets=invalid').has('shot'), false);
  assert.equal(assistantView('?shot=9007199254740997&agent_subject_storyboard=501').get('shot'), '9007199254740997');
});

test('editing selection preserves the project chat and clears only the current object', () => {
  const selected = selectEpisodeObject('?conversation=301&asset=501', 'storyboard', '9007199254740997');
  assert.equal(selected.get('conversation'), '301');
  assert.equal(selected.get('shot'), '9007199254740997');
  assert.equal(selected.get('asset'), '501');
  const cleared = selectEpisodeObject(selected.toString(), 'storyboard', null);
  assert.equal(cleared.has('shot'), false);
  assert.equal(cleared.get('asset'), '501');
  assert.equal(cleared.get('conversation'), '301');
});

test('consecutive editing and assistant updates merge the live URL before React commits', () => {
  const root = '/projects/10/episodes/20';
  const current = { pathname: `${root}/assets`, search: '?assistant=open&conversation=9007199254740993', hash: '#work' };
  const calls = [];
  const navigate = (target, options) => { calls.push({ target, options }); Object.assign(current, target); };
  replaceEpisodeView(root, navigate, previous => selectEpisodeObject(previous.toString(), 'assets', '501'), current);
  replaceEpisodeView(root, navigate, previous => setAssistantOpen(previous.toString(), false), current);
  const view = new URLSearchParams(current.search);
  assert.equal(view.get('asset'), '501');
  assert.equal(view.get('conversation'), '9007199254740993');
  assert.equal(view.has('assistant'), false);
  assert.equal(current.hash, '#work');
  assert.deepEqual(calls.map(call => call.options), [{ replace: true }, { replace: true }]);
  assert.equal(replaceEpisodeView(root, navigate, previous => setAssistantOpen(previous.toString(), false), current), false);
  assert.equal(calls.length, 2);
});

test('an old-stage query callback keeps the live stage and ignores departed episodes', () => {
  const root = '/projects/10/episodes/20';
  const current = { pathname: `${root}/storyboard`, search: '?conversation=301&shot=9007199254740997', hash: '' };
  let target;
  const navigate = next => { target = next; Object.assign(current, next); };
  assert.equal(replaceEpisodeView(root, navigate, previous => setAssistantOpen(previous.toString(), true), current), true);
  assert.equal(target.pathname, `${root}/storyboard`);
  assert.equal(new URLSearchParams(target.search).get('shot'), '9007199254740997');
  for (const pathname of ['/projects/10', '/projects/10/episodes/200/source', '/projects/11/episodes/20/source']) {
    current.pathname = pathname; target = undefined;
    assert.equal(replaceEpisodeView(root, navigate, previous => selectAssistantConversation(previous.toString(), '302'), current), false);
    assert.equal(target, undefined);
  }
});
