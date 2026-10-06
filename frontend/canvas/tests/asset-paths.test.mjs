import assert from "node:assert/strict";
import test from "node:test";
import { assetPrefixes, rebaseAssetLiterals } from "../asset-paths.mjs";

const prefixes = assetPrefixes({
    "public/beef-logo.png": {},
    "public/canvas/models/face.tflite": {},
    "public/three/models/actor.glb": {},
    "public/images/folder-blue.svg": {},
    "src/pages/canvas/project.tsx": {},
});

test("独立画布正确加载静态模型和图标，画布路由与 API 保持各自语义", () => {
    const source = 'const icon="/beef-logo.png"; const model="/three/models/actor.glb"; const route="/canvas/123"; const api="/api/resources/1/file";';
    assert.equal(
        rebaseAssetLiterals(source, prefixes, "/canvas-app/"),
        'const icon="/canvas-app/beef-logo.png"; const model="/canvas-app/three/models/actor.glb"; const route="/canvas/123"; const api="/api/resources/1/file";',
    );
});

test("资源前缀处理可重复运行且不改变外部 URL 或前缀相似的路由", () => {
    const source = '"/canvas-app/beef-logo.png"; "https://example.test/beef-logo.png"; "/beef-logo.png-extra"; "/canvas/models-other/1";';
    assert.equal(rebaseAssetLiterals(source, prefixes, "/canvas-app/"), source);
    const assets = "'/canvas/models/face.tflite'";
    const once = rebaseAssetLiterals(assets, prefixes, "/canvas-app/");
    assert.equal(once, "'/canvas-app/canvas/models/face.tflite'");
    assert.equal(rebaseAssetLiterals(once, prefixes, "/canvas-app/"), once);
});
