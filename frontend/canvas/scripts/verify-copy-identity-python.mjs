import assert from "node:assert/strict";

/** Real APIs prepare a copy fixture; source cross-canvas asset insertion is not exercised. */
export async function verifyCopyIdentity({ browser, context, origin, read, saved, resource, report, recordFailure }) {
    const project = await read(await context.request.post(`${origin}/api/v1/projects`, {
        headers: { "Idempotency-Key": "browser-copy-identity-project" },
        data: { name: "副本身份刷新验证", aspect: "16:9", workspace_mode: "infinite_canvas" },
    }));
    const path = `${origin}/api/v1/projects/${project.id}/canvases/${project.primary_canvas_id}`;
    const initial = await read(await context.request.get(`${path}/my-document`));
    const copy = (await read(await context.request.post(`${origin}/api/v1/canvas-runtime/resources/copies`, {
        headers: { "X-Idempotency-Key": "browser-copy-identity-resource" },
        data: { source_resource_id: resource.id, canvas_key: initial.source_key },
    }))).resource;
    const fileUrl = `/api/v1/canvas-runtime/resources/${copy.id}/file`;
    const document = {
        ...initial.source_document,
        nodes: [{ ...saved, metadata: { ...saved.metadata, storageKey: `resource:${copy.id}`, content: fileUrl } }],
        timeline: {
            version: 2,
            tracks: [{ id: "copy-image-track", kind: "image", label: "图片", order: 0 }],
            clips: [{
                id: "fce66a2c-6bd0-49e1-8b6c-dc2783b6bff7", kind: "image", nodeId: "direct:copy-image", trackId: "copy-image-track", startMs: 0, durationMs: 3000,
                directMedia: { id: "copy-image", kind: "image", title: "同一个素材", assetId: saved.metadata.assetId, storageKey: `resource:${copy.id}`, url: fileUrl, width: copy.width, height: copy.height, bytes: copy.size, mimeType: copy.mimeType },
            }],
            durationMs: 3000,
        },
    };
    await read(await context.request.post(`${path}/commits`, {
        headers: { "Idempotency-Key": "browser-copy-identity-document" },
        data: { expected_row_version: initial.row_version, source_document: document },
    }));
    report.copy_identity_fixture = "authenticated API copy and graph commit; source asset selection/drag-in/paste not exercised";
    const fresh = await browser.newContext({ viewport: { width: 1440, height: 900 }, reducedMotion: report.reduced_motion, storageState: { cookies: await context.cookies(), origins: [] } });
    try {
        const page = await fresh.newPage();
        page.on("pageerror", error => report.page_errors.push(error.message));
        page.on("response", recordFailure);
        await page.goto(`${origin}/canvas-app/canvas/${initial.source_key}`);
        await page.locator(`[data-node-id="${saved.id}"]`).waitFor({ state: "visible" });
        await page.waitForFunction(nodeId => {
            const image = document.querySelector(`[data-node-id="${nodeId}"] img`);
            return image instanceof HTMLImageElement && image.complete && image.naturalWidth === 37 && image.naturalHeight === 19;
        }, saved.id);
        const result = await page.evaluate(async ({ canvasKey, assetId }) => {
            // The source intentionally loads its private library lazily. Use its
            // real batch reader before exercising the complete-library matchers.
            const { loadWorkspaceAssetsForUse } = await import("/canvas-app/src/services/workspace-asset-read.ts");
            await loadWorkspaceAssetsForUse([assetId]);
            const { useCanvasStore } = await import("/canvas-app/src/stores/canvas/use-canvas-store.ts");
            const { useAssetStore } = await import("/canvas-app/src/stores/use-asset-store.ts");
            const { repairMissingCanvasAssets, rebindInconsistentCanvasAssets } = await import("/canvas-app/src/services/canvas-asset-repair.ts");
            const { findCanvasNodeAsset, bindMissingCanvasResourceAssets } = await import("/canvas-app/src/lib/canvas/canvas-node-asset.ts");
            const { ensureCanvasNodeAsset } = await import("/canvas-app/src/services/project-asset-sync.ts");
            const { sameCanvasDocument } = await import("/canvas-app/src/lib/canvas/canvas-content.ts");
            const project = useCanvasStore.getState().projects.find(item => item.id === canvasKey);
            if (!project) throw new Error(`副本验证读取不到已显示画布: ${JSON.stringify({ canvasKey, ids: useCanvasStore.getState().projects.map(item => item.id), hydrated: useCanvasStore.getState().hydrated })}`);
            const assets = useAssetStore.getState().assets;
            const node = project.nodes[0];
            const before = JSON.stringify(project);
            const snapshot = structuredClone(project);
            const found = findCanvasNodeAsset(assets, node, canvasKey)?.id;
            const repair = repairMissingCanvasAssets(new Set([canvasKey]));
            const rebind = rebindInconsistentCanvasAssets(assets, new Set([canvasKey]));
            const repairUnchanged = before === JSON.stringify(useCanvasStore.getState().projects.find(item => item.id === canvasKey));
            let missingCreates = 0;
            const unbound = { ...node, metadata: { ...node.metadata, assetId: undefined } };
            const bound = await bindMissingCanvasResourceAssets([unbound], assets, async () => {
                missingCreates += 1;
                return { assetId: "unexpected-new-asset" };
            });
            const persisted = await ensureCanvasNodeAsset({ canvasId: canvasKey, node, source: "canvas-upload" });
            const current = useCanvasStore.getState().projects.find(item => item.id === canvasKey);
            return {
                found, repair, rebind, missingCreates, missingId: bound[0].metadata.assetId, persisted,
                repairUnchanged, documentUnchanged: sameCanvasDocument(snapshot, current),
                changedFields: Object.keys(current).filter(key => JSON.stringify(snapshot[key]) !== JSON.stringify(current[key])),
                nodeAsset: current.nodes[0].metadata.assetId,
                clipAsset: current.timeline.clips[0].directMedia.assetId,
                originalKey: useAssetStore.getState().assets.find(item => item.id === assetId)?.data.storageKey,
                assetIds: useAssetStore.getState().assets.map(item => item.id),
            };
        }, { canvasKey: initial.source_key, assetId: saved.metadata.assetId });
        assert.equal(result.found, saved.metadata.assetId);
        assert.deepEqual(result.repair, { createdAssets: 0, updatedProjects: 0 });
        assert.deepEqual(result.rebind, { reboundNodes: 0, createdAssets: 0, updatedProjects: 0 });
        assert.equal(result.missingCreates, 0, "an unbound copy must reuse the original asset, including the source history binder");
        assert.equal(result.missingId, saved.metadata.assetId);
        assert.equal(result.persisted.created, false);
        assert.equal(result.persisted.assetId, saved.metadata.assetId);
        assert.ok(result.repairUnchanged, "synchronous source repair must leave the entire project unchanged");
        assert.ok(result.documentUnchanged, `source save binding must leave document content unchanged: ${result.changedFields}`);
        assert.equal(result.nodeAsset, saved.metadata.assetId);
        assert.equal(result.clipAsset, saved.metadata.assetId);
        assert.equal(result.originalKey, `resource:${resource.id}`);
        assert.deepEqual(result.assetIds, [saved.metadata.assetId]);
        const library = await read(await fresh.request.get(`${origin}/api/v1/canvas-runtime/assets`));
        assert.deepEqual(library.assets.map(asset => asset.id), [saved.metadata.assetId]);
        const asset = (await read(await fresh.request.get(`${origin}/api/v1/canvas-runtime/assets/${saved.metadata.assetId}`))).asset;
        assert.equal(asset.data.storageKey, `resource:${resource.id}`);
        const reread = await read(await fresh.request.get(`${path}/my-document`));
        assert.equal(reread.source_document.nodes[0].metadata.storageKey, `resource:${copy.id}`);
        report.copy_identity_restored = true;
        report.copy_source_matchers_unchanged = true;
        // With the API fixture loaded, exercise the original drag/save operation.
        const node = page.locator(`[data-node-id="${saved.id}"]`);
        const before = await node.boundingBox();
        const originalPosition = reread.source_document.nodes[0].position;
        await Promise.all([
            page.waitForResponse(response => {
                if (response.url() !== `${path}/commits` || !response.ok()) return false;
                const changed = response.request().postDataJSON()?.source_document.nodes.find(item => item.id === saved.id);
                return changed && (changed.position.x !== originalPosition.x || changed.position.y !== originalPosition.y);
            }),
            (async () => {
                await page.mouse.move(before.x + 20, before.y + 8);
                await page.mouse.down();
                await page.mouse.move(before.x + 130, before.y + 68, { steps: 12 });
                await page.mouse.up();
            })(),
        ]);
        const edited = await read(await fresh.request.get(`${path}/my-document`));
        assert.notDeepEqual(edited.source_document.nodes[0].position, originalPosition);
        assert.equal(edited.source_document.nodes[0].metadata.assetId, saved.metadata.assetId);
        assert.equal(edited.source_document.nodes[0].metadata.storageKey, `resource:${copy.id}`);
        assert.equal(edited.source_document.timeline.clips[0].directMedia.assetId, saved.metadata.assetId);
        await page.reload();
        await node.waitFor({ state: "visible" });
        await page.waitForFunction(nodeId => {
            const image = document.querySelector(`[data-node-id="${nodeId}"] img`);
            return image instanceof HTMLImageElement && image.complete && image.naturalWidth === 37;
        }, saved.id);
        const restored = await read(await fresh.request.get(`${path}/my-document`));
        assert.deepEqual(restored.source_document.nodes[0].position, edited.source_document.nodes[0].position);
        assert.equal(restored.source_document.nodes[0].metadata.assetId, saved.metadata.assetId);
        report.copy_edit_saved_and_refreshed = true;
    } finally {
        await fresh.close();
    }
}
