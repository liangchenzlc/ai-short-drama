import assert from "node:assert/strict";
import { writeFile } from "node:fs/promises";
import { resolve } from "node:path";

/** The original library and top-bar menus initiate both forms of duplication. */
export async function verifyCreation({ browser, context, origin, read, original, saved, resource, report, recordFailure }) {
    const fresh = await browser.newContext({ viewport: { width: 1440, height: 900 }, reducedMotion: report.reduced_motion, storageState: { cookies: await context.cookies(), origins: [] } });
    fresh.setDefaultTimeout(15000);
    const page = await fresh.newPage();
    page.on("pageerror", error => report.page_errors.push(error.message));
    page.on("response", recordFailure);
    try {
        report.creation_step = "source project rename before copy";
        const sourceTitle = "完整画布复制来源";
        await page.goto(`${origin}/canvas-app/canvas/${original.source_key}`);
        await page.getByRole("button", { name: original.title, exact: true }).click();
        await page.getByRole("textbox", { name: "画布名称", exact: true }).fill(sourceTitle);
        const renamed = page.waitForResponse(response => response.request().method() === "POST" && new URL(response.url()).pathname.endsWith("/commits") && response.request().postDataJSON()?.source_document?.title === sourceTitle);
        await page.getByRole("textbox", { name: "画布名称", exact: true }).press("Enter");
        await read(await renamed);
        report.source_project_rename_saved = true;
        report.creation_step = "source library create-copy menu";
        await page.goto(`${origin}/canvas-app/canvas`);
        await page.getByRole("button", { name: `${sourceTitle} 画布操作`, exact: true }).click();
        const created = page.waitForResponse(response => new URL(response.url()).pathname === "/api/v1/canvas-workspace" && response.request().method() === "POST");
        await page.getByRole("menuitem", { name: "创建副本", exact: true }).click();
        const response = await created;
        const request = response.request().postDataJSON();
        const copied = await read(response);
        assert.equal(request.source_document.nodes[0].metadata.storageKey, `resource:${resource.id}`);
        const targetId = copied.resource_map[resource.id];
        assert.ok(targetId && targetId !== resource.id);
        assert.notEqual(copied.project_id, original.project_id);
        assert.ok(copied.resource_aliases[targetId].includes(resource.id));
        await page.waitForURL(`**/canvas/${copied.source_key}`);
        await page.locator(`[data-node-id="${saved.id}"]`).waitFor();
        const path = `${origin}/api/v1/projects/${copied.project_id}/canvases/${copied.id}`;
        const persisted = await read(await fresh.request.get(`${path}/my-document`));
        assert.equal(persisted.source_document.nodes.length, 1);
        assert.equal(persisted.source_document.nodes[0].metadata.storageKey, `resource:${targetId}`);
        assert.equal(persisted.source_document.nodes[0].metadata.assetId, saved.metadata.assetId);
        assert.deepEqual(persisted.source_document.nodes[0].position, saved.position);
        const live = await page.evaluate(async key => {
            const { useCanvasStore } = await import("/canvas-app/src/stores/canvas/use-canvas-store.ts");
            return structuredClone(useCanvasStore.getState().projects.find(item => item.id === key));
        }, copied.source_key);
        assert.equal(live.workspaceProjectId, copied.project_id);
        assert.equal(live.nodes[0].metadata.storageKey, `resource:${targetId}`);
        assert.equal(live.nodes[0].metadata.assetId, saved.metadata.assetId);
        const file = await fresh.request.get(`${origin}/api/v1/canvas-runtime/resources/${targetId}/file`);
        const source = await fresh.request.get(`${origin}/api/v1/canvas-runtime/resources/${resource.id}/file`);
        assert.deepEqual(await file.body(), await source.body());
        report.source_project_copy_atomic = true;

        report.creation_step = "source top-bar duplicate-canvas menu";
        await page.getByRole("button", { name: "切换画布", exact: true }).click();
        await page.locator(".canvas-topbar-canvas-menu-row").hover({ position: { x: 20, y: 20 } });
        await page.getByRole("button", { name: /^画布操作：/ }).hover();
        const extra = page.waitForResponse(response => new URL(response.url()).pathname === `/api/v1/projects/${copied.project_id}/canvases` && response.request().method() === "POST");
        await page.getByRole("menuitem", { name: "复制画布", exact: true }).click();
        const sibling = await read(await extra);
        assert.equal(sibling.project_id, copied.project_id);
        assert.notEqual(sibling.id, copied.id);
        assert.equal(sibling.resource_map[targetId], targetId);
        const siblingPath = `${origin}/api/v1/projects/${sibling.project_id}/canvases/${sibling.id}`;
        const siblingDocument = await read(await fresh.request.get(`${siblingPath}/my-document`));
        assert.deepEqual(siblingDocument.source_document.nodes, persisted.source_document.nodes);
        report.source_same_project_copy_reuses_media = true;

        report.creation_step = "source multi-canvas project copy";
        await page.goto(`${origin}/canvas-app/canvas`);
        await page.getByRole("button", { name: `${copied.title} 画布操作`, exact: true }).click();
        const firstOfGroup = page.waitForResponse(response => new URL(response.url()).pathname === "/api/v1/canvas-workspace" && response.request().method() === "POST");
        const secondOfGroup = page.waitForResponse(response => /^\/api\/v1\/projects\/\d+\/canvases$/.test(new URL(response.url()).pathname) && !response.url().includes(`/projects/${copied.project_id}/`) && response.request().method() === "POST");
        await page.getByRole("menuitem", { name: "创建副本", exact: true }).click();
        const groupRoot = await read(await firstOfGroup);
        const groupChild = await read(await secondOfGroup);
        assert.equal(groupRoot.project_id, groupChild.project_id);
        assert.notEqual(groupRoot.id, groupChild.id);
        assert.notEqual(groupRoot.project_id, copied.project_id);
        await page.waitForURL(`**/canvas/${groupRoot.source_key}`);
        const grouped = await read(await fresh.request.get(`${origin}/api/v1/projects/${groupRoot.project_id}/canvases`));
        assert.equal(grouped.items.length, 2);
        report.source_multi_canvas_copy_group_preserved = true;

        report.creation_step = "copy reload without browser storage";
        const clean = await browser.newContext({ viewport: { width: 1440, height: 900 }, reducedMotion: report.reduced_motion, storageState: { cookies: await context.cookies(), origins: [] } });
        try {
            const reloaded = await clean.newPage();
            reloaded.on("pageerror", error => report.page_errors.push(error.message));
            reloaded.on("response", recordFailure);
            await reloaded.goto(`${origin}/canvas-app/canvas/${copied.source_key}`);
            await reloaded.locator(`[data-node-id="${saved.id}"]`).waitFor();
            await reloaded.waitForFunction(id => {
                const image = document.querySelector(`[data-node-id="${id}"] img`);
                return image instanceof HTMLImageElement && image.complete && image.naturalWidth === 37;
            }, saved.id);
            const restored = await read(await clean.request.get(`${path}/my-document`));
            assert.deepEqual(restored.source_document.nodes, persisted.source_document.nodes);
            const library = await read(await clean.request.get(`${origin}/api/v1/canvas-runtime/assets`));
            assert.deepEqual(library.assets.map(asset => asset.id), [saved.metadata.assetId]);
            report.source_project_copy_fresh_context_restored = true;
        } finally {
            await clean.close();
        }
        report.creation_step = "complete";
    } catch (error) {
        await page.screenshot({ path: resolve(".runtime/resource-python-browser/creation-failure.png") }).catch(() => {});
        await writeFile(resolve(".runtime/resource-python-browser/creation-failure-dom.txt"), await page.locator("body").ariaSnapshot()).catch(() => {});
        throw error;
    } finally {
        await fresh.close();
    }
}
