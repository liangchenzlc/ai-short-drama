import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import ts from 'typescript';

const source = readFileSync(new URL('../src/features/auth/account-storage.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText;
const { accountStorage } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);
test('switching accounts isolates editor drafts and ignores unowned legacy data', () => {
  const values = new Map([['episode:10', 'unowned-old-draft']]);
  const storage = { getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key) };
  const author = accountStorage('11', storage), collaborator = accountStorage('12', storage);
  assert.equal(author.getItem('episode:10'), null);
  author.setItem('episode:10', 'personal-unsaved-story');
  assert.equal(collaborator.getItem('episode:10'), null);
  collaborator.setItem('episode:10', 'another-account-story');
  assert.equal(author.getItem('episode:10'), 'personal-unsaved-story');
  author.removeItem('episode:10');
  assert.equal(collaborator.getItem('episode:10'), 'another-account-story');
  assert.equal(storage.getItem('episode:10'), 'unowned-old-draft');
});
