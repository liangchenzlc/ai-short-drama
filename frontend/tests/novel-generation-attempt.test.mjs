import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import ts from 'typescript';

const source = ts.transpileModule(readFileSync(new URL('../src/features/projects/novel-generation-attempt.ts', import.meta.url), 'utf8'), {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 },
}).outputText;
const { isNovelGenerationAttempt } = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
const valid = () => ({ format: 1, key: 'a6379190-b682-4b75-9fe2-814b37cc97dd', body: { config_id: '9007199254740993', source: { scene: 'novel_script', project_id: '10', episode_id: '20', content_version: '3', draft_id: '30', draft_version: '4' }, instructions: '保留主角对白', parameters: { max_output_tokens: 8192 } } });

test('frozen adaptation request retains exact string versions and original key', () => {
  const request = valid(); assert.equal(isNovelGenerationAttempt(request), true);
  assert.deepEqual(JSON.parse(JSON.stringify(request)), request);
});

test('corrupt or mixed workflow records cannot become a new adaptation request', () => {
  for (const mutate of [
    value => { value.body.source.scene = 'shot_video'; },
    value => { value.body.source.draft_version = 4; },
    value => { delete value.body.source.draft_id; },
    value => { value.body.parameters.max_output_tokens = 1; },
    value => { value.body.input = { messages: [] }; },
    value => { value.body.api_key = 'secret'; },
    value => { value.body.source.signed_url = 'https://example.invalid/signed'; },
    value => { value.key = 'invalid'; },
  ]) {
    const request = valid(); mutate(request); assert.equal(isNovelGenerationAttempt(request), false);
  }
});
