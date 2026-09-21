import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import ts from 'typescript';

const components = [
  '../src/features/projects/NovelScriptGeneration.tsx',
  '../src/features/projects/ShotImageCandidates.tsx',
  '../src/pages/projects/episode/StoryboardStage.tsx',
];

test('workflow task rows present statuses through the shared task label', () => {
  for (const component of components) {
    const source = readFileSync(new URL(component, import.meta.url), 'utf8');
    const file = ts.createSourceFile(component, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
    let sharedLabels = 0;
    let rawStatusExpressions = 0;
    function visit(node) {
      if (ts.isCallExpression(node)
        && ts.isIdentifier(node.expression)
        && node.expression.text === 'taskLabel'
        && node.arguments.length === 1
        && ts.isIdentifier(node.arguments[0])
        && node.arguments[0].text === 'task') sharedLabels += 1;
      if (ts.isJsxExpression(node)
        && node.expression
        && ts.isPropertyAccessExpression(node.expression)
        && ts.isIdentifier(node.expression.expression)
        && node.expression.expression.text === 'task'
        && node.expression.name.text === 'status') rawStatusExpressions += 1;
      ts.forEachChild(node, visit);
    }
    visit(file);
    assert.equal(sharedLabels, 1, `${component} must render taskLabel(task)`);
    assert.equal(rawStatusExpressions, 0, `${component} must not render task.status directly`);
  }
});
