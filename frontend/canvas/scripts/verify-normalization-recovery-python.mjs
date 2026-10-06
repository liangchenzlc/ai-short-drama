import assert from "node:assert/strict";

/** A simulated 503 before commit delivery exercises the real browser's durable retry journal. */
export async function verifyNormalizationRecovery({ browser, context, origin, read, saved, resource, report, recordFailure }) {
    const project = await read(await context.request.post(`${origin}/api/v1/projects`, {
        headers: { "Idempotency-Key": "browser-normalization-recovery" },
        data: { name: "资源映射草稿恢复", aspect: "16:9", workspace_mode: "infinite_canvas" },
    }));
    const path = `${origin}/api/v1/projects/${project.id}/canvases/${project.primary_canvas_id}`;
    const initial = await read(await context.request.get(`${path}/my-document`));
    const fresh = await browser.newContext({ viewport: { width: 1440, height: 900 }, reducedMotion: report.reduced_motion, storageState: { cookies: await context.cookies(), origins: [] } });
    fresh.setDefaultTimeout(15000);
    try {
        const page = await fresh.newPage();
        page.on("pageerror", error => report.page_errors.push(error.message));
        page.on("response", recordFailure);
        const attempts = [];
        await page.route(`${path}/commits`, async route => {
            attempts.push({ key: route.request().headers()["idempotency-key"], body: route.request().postDataJSON() });
            await route.fulfill({ status: 503, contentType: "application/json", json: { error: { code: "test_delivery_unavailable", message: "测试注入：图提交尚未送达，保留原请求重试" } } });
        });
        await page.goto(`${origin}/canvas-app/canvas/${initial.source_key}`);
        await page.locator("[data-canvas-viewport]").click({ button: "right", position: { x: 550, y: 250 } });
        await page.getByRole("button", { name: "从素材库插入", exact: true }).click();
        const picker = page.locator(".asset-library-picker-modal");
        await picker.locator(".asset-picker-card-action").click();
        const failed = page.waitForResponse(response => response.url() === `${path}/commits` && response.status() === 503);
        await picker.getByRole("button", { name: "插入已选素材（1）", exact: true }).click();
        await failed;
        const frozen = structuredClone(attempts[0]);
        const node = frozen.body.source_document.nodes[0];
        assert.notEqual(node.metadata.storageKey, `resource:${resource.id}`);
        await page.evaluate(async () => {
            const { flushCanvasStorePersistence } = await import("/canvas-app/src/stores/canvas/use-canvas-store.ts");
            await flushCanvasStorePersistence();
        });
        assert.deepEqual((await read(await fresh.request.get(`${path}/my-document`))).source_document.nodes, []);
        await page.reload();
        await page.locator(`[data-node-id="${node.id}"]`).waitFor({ state: "visible" });
        const restored = await page.evaluate(async ({ key, assetId }) => {
            const { loadWorkspaceAssetsForUse } = await import("/canvas-app/src/services/workspace-asset-read.ts");
            await loadWorkspaceAssetsForUse([assetId]);
            const { useCanvasStore } = await import("/canvas-app/src/stores/canvas/use-canvas-store.ts");
            const { useAssetStore } = await import("/canvas-app/src/stores/use-asset-store.ts");
            const { findCanvasNodeAsset } = await import("/canvas-app/src/lib/canvas/canvas-node-asset.ts");
            const { peekCanvasOperationJournal } = await import("/canvas-app/src/services/canvas-operation-journal.ts");
            const current = useCanvasStore.getState().projects.find(item => item.id === key);
            return { found: findCanvasNodeAsset(useAssetStore.getState().assets, current.nodes[0], key)?.id, node: current.nodes[0], journal: peekCanvasOperationJournal(key) };
        }, { key: initial.source_key, assetId: saved.metadata.assetId });
        assert.equal(restored.found, saved.metadata.assetId, "an uncommitted normalized draft must restore private copy provenance before source matchers run");
        assert.equal(restored.node.metadata.storageKey, node.metadata.storageKey);
        assert.equal(restored.journal.inFlight.operationId, frozen.key);
        assert.deepEqual(restored.journal.inFlight.payload.document, frozen.body.source_document);
        await page.unroute(`${path}/commits`);
        const accepted = page.waitForResponse(response => response.url() === `${path}/commits` && response.ok());
        await page.keyboard.press("Control+s");
        const response = await accepted;
        assert.equal(response.request().headers()["idempotency-key"], frozen.key);
        assert.deepEqual(response.request().postDataJSON(), frozen.body);
        const final = await read(await fresh.request.get(`${path}/my-document`));
        assert.equal(final.source_document.nodes[0].metadata.assetId, saved.metadata.assetId);
        assert.equal(final.source_document.nodes[0].metadata.storageKey, node.metadata.storageKey);
        const library = await read(await fresh.request.get(`${origin}/api/v1/canvas-runtime/assets`));
        assert.deepEqual(library.assets.map(asset => asset.id), [saved.metadata.assetId]);
        report.normalization_draft_provenance_restored = true;
        report.normalization_retry_preserves_key_and_body = true;
        report.normalization_recovery_fault = "Playwright 503 before commit delivery; resources and successful retry use real Python/MySQL/MinIO";
    } finally {
        await fresh.close();
    }
}
