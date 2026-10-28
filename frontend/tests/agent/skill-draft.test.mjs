import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import ts from 'typescript';

const source = readFileSync(new URL('../../src/features/skills/skill-draft.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText;
const { createSkillDraft, skillDraftIssue, skillContent, skillResourcePathIssue, readSkillDraft, saveSkillDraft,
  clearSkillDraft, rawSkillDraft, readSkillAction, saveSkillAction } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);
function storage() {
  const values = new Map();
  return { values, getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key) };
}
function validDraft() {
  const draft = createSkillDraft();
  Object.assign(draft.values, { name: '分镜检查', description: '检查用户提供的分镜。', instructions: '# 检查\n保持人物连续性。' });
  draft.changed = true; return draft;
}
function detail() {
  return { id: '9007199254740995', name: '分镜检查', filename: 'shot.md', builtin: false, content_version: '3', row_version: '9007199254740993',
    settings_version: '9007199254740997', enabled: false, invocation_mode: 'manual', slug: 'shot', description: '检查已选内容。',
    instructions: '# 检查', resources: [{ path: 'references/notes.md', kind: 'reference', checksum_sha256: 'a'.repeat(64), byte_size: 30 }],
    tags: [], modes: ['standard', 'infinite_canvas'], exclusions: '', required_tools: [], source: '', compatible: true, issues: [],
    checksum_sha256: 'b'.repeat(64), package_checksum: 'c'.repeat(64), resource_count: 1, updated_at: null };
}

test('readonly main-file content hides legacy metadata and preserves Markdown separators', async () => {
  const model = readFileSync(new URL('../../src/features/skills/skill-model.ts', import.meta.url), 'utf8');
  const compiledModel = ts.transpileModule(model, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText;
  const { instructionBody } = await import(`data:text/javascript;base64,${Buffer.from(compiledModel).toString('base64')}`);
  assert.equal(instructionBody('---\nname: method\ndescription: 检查\n---\n# 正文'), '# 正文');
  const markdown = '---\n开场\n---\n后续剧情';
  assert.equal(instructionBody(markdown), markdown);
});

test('new and imported drafts use personal defaults while editing preserves saved settings and string versions', () => {
  const fresh = validDraft();
  assert.equal(fresh.values.enabled, true); assert.equal('invocation_mode' in fresh.values, false);
  const existing = createSkillDraft(detail());
  assert.equal(existing.values.enabled, false); assert.equal('invocation_mode' in existing.values, false);
  assert.equal(existing.base.row_version, '9007199254740993');
  assert.equal(existing.values.resources[0].content, null); assert.equal(existing.values.resources[0].origin_version, '3');
  assert.throws(() => skillContent(existing.values), /尚未读取/);
  assert.equal('invocation_mode' in skillContent({ ...fresh.values, invocation_mode: 'manual' }), false);
});

test('a pending create survives refresh under the same account and refuses a replacement request', () => {
  const disk = storage(); const draft = validDraft();
  draft.pending = { key: draft.key, row_version: null, body: skillContent(draft.values) };
  assert.equal(saveSkillDraft('owner', draft, disk), true);
  const restored = readSkillDraft('owner', disk).draft;
  assert.deepEqual(restored.pending, draft.pending); assert.equal(readSkillDraft('another', disk).draft, null);
  const replacement = structuredClone(draft); replacement.pending.body.instructions = '另一份指令';
  assert.equal(saveSkillDraft('owner', replacement, disk), false);
  assert.equal(saveSkillDraft('owner', { ...draft, pending: null }, disk), false);
  assert.deepEqual(readSkillDraft('owner', disk).draft.pending, draft.pending);
  assert.equal(saveSkillDraft('owner', { ...draft, pending: null, conflict: true }, disk, true), true);
  assert.equal(clearSkillDraft('another', disk), true); assert.ok(readSkillDraft('owner', disk).draft);
});

test('malformed drafts retain the original bytes and cannot be silently replaced', () => {
  const disk = storage(); const draft = validDraft(); saveSkillDraft('owner', draft, disk);
  const key = [...disk.values.keys()][0]; disk.setItem(key, '{broken');
  assert.match(readSkillDraft('owner', disk).error, /原记录已保留/);
  assert.equal(saveSkillDraft('owner', validDraft(), disk), false); assert.equal(rawSkillDraft('owner', disk), '{broken');
  assert.equal(clearSkillDraft('owner', disk), true); assert.equal(saveSkillDraft('owner', draft, disk), true);
  const invalid = createSkillDraft(detail()); invalid.base.modes = null;
  disk.setItem(key, JSON.stringify(invalid)); assert.equal(readSkillDraft('owner', disk).draft, null);
});

test('local storage failures stop request persistence and leave the editable draft in memory', () => {
  const draft = validDraft(); const disk = { getItem: () => null, setItem: () => { throw new Error('quota'); }, removeItem: () => { throw new Error('locked'); } };
  assert.equal(saveSkillDraft('owner', draft, disk), false); assert.equal(clearSkillDraft('owner', disk), false);
  assert.equal(draft.values.instructions, '# 检查\n保持人物连续性。');
});

test('UTF-8 and normalized path bounds reject an invalid package before publishing', () => {
  const { values } = validDraft(); values.instructions = '中'.repeat(21846);
  assert.match(skillDraftIssue(values), /64 KiB/); values.instructions = '# 检查';
  values.resources = [{ path: 'references/é.md', kind: 'reference', content: '' }, { path: 'references/e\u0301.md', kind: 'template', content: '' }];
  assert.match(skillDraftIssue(values), /不能重复/);
  values.resources = [{ path: 'references/notes.md', kind: 'reference', content: '中'.repeat(43691) }];
  assert.match(skillDraftIssue(values), /128 KiB/);
  for (const path of ['../notes.md', '/notes.md', 'C:/notes.md', 'references\\notes.md', 'a/b/c/d/e/f/g.md', 'SKILL.md', 'scripts/run.exe']) assert.ok(skillResourcePathIssue(path), path);
  assert.ok(skillResourcePathIssue('scripts/run.py'));
  assert.equal(skillResourcePathIssue('assets/shot-template.json'), '');
});

test('submissions contain text resources without cache or storage implementation fields', () => {
  const { values } = validDraft(); values.name = '  分镜检查  ';
  values.resources = [{ path: 'assets/e\u0301.json', kind: 'template', content: '{}', origin_version: '3', checksum_sha256: 'a'.repeat(64) }];
  const content = skillContent(values); assert.equal(content.name, '分镜检查');
  assert.deepEqual(content.resources, [{ path: 'assets/é.json', kind: 'template', content: '{}', encoding: 'utf-8' }]);
});

test('legacy scripts remain recoverable but new submissions reject them; images preserve binary transport', () => {
  const disk = storage(); const draft = validDraft();
  draft.values.resources = [
    { path: 'scripts/check.py', kind: 'script', content: 'raise RuntimeError("read-only")' },
    { path: 'assets/example.png', kind: 'image', content: btoa('managed-image-bytes'), encoding: 'base64' },
  ];
  assert.match(skillDraftIssue(draft.values), /暂不支持脚本/);
  assert.equal(saveSkillDraft('legacy', draft, disk), true);
  assert.equal(readSkillDraft('legacy', disk).draft.values.resources[0].kind, 'script');
  draft.values.resources.shift();
  draft.pending = { key: draft.key, row_version: null, body: skillContent(draft.values) };
  assert.equal(saveSkillDraft('owner', draft, disk), true);
  assert.deepEqual(readSkillDraft('owner', disk).draft.pending, draft.pending);
  assert.equal(draft.pending.body.resources[0].encoding, 'base64');
  draft.values.resources[0].content = 'invalid$base64';
  assert.match(skillDraftIssue(draft.values), /内容无效/);
});

test('copy, settings and deletion recovery keep account-scoped identities without rounding', () => {
  const disk = storage(); const item = detail();
  for (const action of [{ kind: 'copy', item, key: crypto.randomUUID() }, { kind: 'settings', item, enabled: true }, { kind: 'mode', item, modes: ['standard'] }, { kind: 'delete', item }]) {
    assert.equal(saveSkillAction('owner', action, disk), true);
    const { instructions: _instructions, resources: _resources, ...summary } = item;
    assert.deepEqual(readSkillAction('owner', disk).action, { ...action, item: summary }); assert.equal(readSkillAction('other', disk).action, null);
  }
  const key = [...disk.values.keys()][0]; disk.setItem(key, JSON.stringify({ kind: 'copy', item, key: null }));
  assert.match(readSkillAction('owner', disk).error, /原记录已保留/);
});

test('retired settings requests retain their original bytes and cannot be rewritten for replay', () => {
  const disk = storage(); const key = 'agent-skill-action:v1:owner';
  const legacy = { kind: 'settings', item: detail(), enabled: true, invocation_mode: 'manual' };
  const raw = JSON.stringify(legacy); disk.setItem(key, raw);
  assert.equal(readSkillAction('owner', disk).action, null);
  assert.match(readSkillAction('owner', disk).error, /原记录已保留/);
  assert.equal(disk.getItem(key), raw);
  assert.equal(saveSkillAction('owner', legacy, disk), false);
});
