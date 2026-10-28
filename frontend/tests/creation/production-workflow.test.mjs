import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import ts from 'typescript';

const source = readFileSync(new URL('../../src/features/creation/shared/model/workflow-contract.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText;
const workflow = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);

test('storyboard exposes the persisted image model selection', () => {
  const component = '../../src/features/creation/storyboard/images/ShotImageCandidates.tsx';
  const componentSource = readFileSync(new URL(component, import.meta.url), 'utf8');
  const file = ts.createSourceFile(component, componentSource, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  let imageSelect;
  function visit(node) {
    if (ts.isJsxSelfClosingElement(node) && node.tagName.getText(file) === 'CreationModelSelect') {
      const attributes = Object.fromEntries(node.attributes.properties
        .filter(ts.isJsxAttribute)
        .map((attribute) => [attribute.name.getText(file), attribute.initializer?.getText(file)]));
      if (attributes.kind === '"image"') imageSelect = attributes;
    }
    ts.forEachChild(node, visit);
  }
  visit(file);
  assert.ok(imageSelect, 'missing image CreationModelSelect');
  assert.equal(imageSelect.label, '"分镜生图模型"');
  assert.equal(imageSelect.value, '{draft.value.model_config_id ?? modelSelection}');
  assert.match(imageSelect.onChange, /draft.edit\(\{ model_config_id: id \|\| null \}\)/);
  assert.match(imageSelect.onChange, /onModelChange\(id\)/);
  assert.equal(imageSelect.onResolvedChange, '{handleResolved}');
});
test('storyboard generation payloads use saved server versions without client messages', () => {
  assert.deepEqual(workflow.scriptShotsRequest('12', '78', '56', ''), {
    source: { scene: 'script_shots', project_id: '12', script_id: '78', content_version: '56' },
    storyboard: { average_shot_duration_ms: 3000 },
    instructions: '', parameters: { max_output_tokens: 8192 },
  });
  assert.deepEqual(workflow.scriptShotsRequest('12', '78', '56', '', 5000), {
    source: { scene: 'script_shots', project_id: '12', script_id: '78', content_version: '56' },
    storyboard: { average_shot_duration_ms: 5000 },
    instructions: '', parameters: { max_output_tokens: 8192 },
  });
});

test('storyboard timing summarizes generated beats and defaults legacy durations', () => {
  assert.deepEqual(workflow.storyboardTiming([
    { duration_ms: 2000 },
    { duration_ms: 4000 },
    {},
  ]), { total_ms: 9000, average_ms: 3000 });
  assert.deepEqual(workflow.storyboardTiming([]), { total_ms: 0, average_ms: 0 });
});

test('storyboard review preserves edited timing and loads compact result pages', async () => {
  const preview = readFileSync(new URL('../../src/features/creation/storyboard/history/StoryboardResultPreview.tsx', import.meta.url), 'utf8');
  const editingSource = readFileSync(new URL('../../src/features/creation/storyboard/sessions/editing-session.ts', import.meta.url), 'utf8');
  const editingFile = ts.createSourceFile('storyboard-editing-session.ts', editingSource, ts.ScriptTarget.Latest, true);
  const draftFunction = editingFile.statements.find(node => ts.isFunctionDeclaration(node) && node.name?.text === 'shotDraftValue');
  assert.ok(draftFunction);
  const draftSource = ts.createPrinter().printNode(ts.EmitHint.Unspecified, draftFunction, editingFile);
  const draftCompiled = ts.transpileModule(draftSource, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText;
  const { shotDraftValue } = await import(`data:text/javascript;base64,${Buffer.from(draftCompiled).toString('base64')}`);
  const edited = { title: '短镜头', script: '雨落', duration_ms: 4500, asset_ids: ['9007199254740993'], image_settings: { aspect: '16:9' }, video_prompt: '推近', video_settings: { duration_ms: 5000 } };
  assert.deepEqual(shotDraftValue({ ...edited, id: '42', row_version: '9' }), edited);
  assert.match(preview, /resultShots/);
  assert.match(preview, /LazyLoadMore/);
  assert.match(preview, /story_beat/);
  assert.match(preview, /source_excerpt/);
  assert.match(preview, /total_duration_ms/);
});

test('saved shot image payload carries all concurrency and settings inputs', () => {
  assert.deepEqual(workflow.shotImageRequest({ id: '91', row_version: '4', context_hash: 'a'.repeat(64), image_settings: { layout: 'four', aspect: '16:9', resolution: '2K' } }, 'more rain', ['7', '7', '8'], 2), {
    source: { scene: 'shot_image', shot_id: '91', layout: 'four', context_mode: 'saved', row_version: '4', context_hash: 'a'.repeat(64) },
    input: { prompt: 'more rain', reference_media_ids: ['7', '8'] },
    parameters: { aspect: '16:9', resolution: '2K', count: 2 },
  });
});

test('saved shot image keeps a model-default aspect unset without project inheritance', () => {
  const shot = { id: '91', row_version: '4', context_hash: 'c'.repeat(64), image_settings: { layout: 'single', aspect: null, resolution: '1K' } };
  const request = workflow.shotImageRequest(shot, '', [], 1);
  assert.equal('aspect' in request.parameters, false);
  assert.equal(request.source.layout, 'single');
  assert.equal('parameters' in workflow.shotImageApplyRequest(shot, false), false);
});

test('shot image apply cannot omit row, media, or context compare tokens', () => {
  assert.deepEqual(workflow.shotImageApplyRequest({ id: '91', row_version: '4', context_hash: 'b'.repeat(64), image: { media_id: '11' }, image_settings: { layout: 'single', aspect: '9:16', resolution: '4K' } }, false), {
    target: { type: 'shot_image', id: '91' }, expected_media_id: '11', expected_row_version: '4', expected_context_hash: 'b'.repeat(64), acknowledge_stale_source: false,
  });
});

test('reorder is total and preserves decimal string ids', () => {
  const shots = [{ id: '90071992547409931' }, { id: '90071992547409932' }, { id: '90071992547409933' }];
  assert.deepEqual(workflow.moveShot(shots, '90071992547409932', -1).map((shot) => shot.id), ['90071992547409932', '90071992547409931', '90071992547409933']);
  assert.deepEqual(workflow.moveShot(shots, '90071992547409931', -1), shots);
});

test('asset patch and confirmation preserve explicit compare tokens', () => {
  assert.deepEqual(workflow.assetPatch({ row_version: '9', name: '  Lin  ', tags: [' lead ', 'lead', ''] }, true), { row_version: '9', name: 'Lin', tags: ['lead'] });
  assert.deepEqual(workflow.assetConfirmRequest({ row_version: '9', media_id: '23' }, '44', true), { row_version: '9', media_id: '44', expected_media_id: '23' });
});

test('asset editor hides the image gallery until an adopted image or candidate exists', () => {
  assert.equal(typeof workflow.assetImagePresentation, 'function');
  assert.deepEqual(workflow.assetImagePresentation(null, null, []), {
    visible: false,
    current: null,
    alternatives: [],
  });

  const current = { media_id: '11', url: '/current.webp' };
  const duplicate = { id: 'candidate-current', media_id: '11', url: '/current.webp' };
  const alternative = { id: 'candidate-new', media_id: '12', url: '/new.webp' };
  assert.deepEqual(workflow.assetImagePresentation('11', current, [alternative, duplicate]), {
    visible: true,
    current,
    alternatives: [alternative],
  });

  assert.deepEqual(workflow.assetImagePresentation('11', null, [alternative, duplicate]), {
    visible: true,
    current: duplicate,
    alternatives: [alternative],
  });

  assert.deepEqual(workflow.assetImagePresentation(null, null, [alternative]), {
    visible: true,
    current: null,
    alternatives: [alternative],
  });
});
