import assert from "node:assert/strict";
import { writeFile } from "node:fs/promises";
import { resolve } from "node:path";

/** Prepare only an empty destination; all inserted nodes come from the source UI. */
export async function verifyNormalization({ browser, context, origin, read, saved, resource, report, recordFailure }) {
    const project = await read(await context.request.post(`${origin}/api/v1/projects`, {
        headers: { "Idempotency-Key": "browser-normalization-project" },
        data: { name: "素材插入与保存验证", aspect: "16:9", workspace_mode: "infinite_canvas" },
    }));
    const path = `${origin}/api/v1/projects/${project.id}/canvases/${project.primary_canvas_id}`;
    const initial = await read(await context.request.get(`${path}/my-document`));
    assert.deepEqual(initial.source_document.nodes, []);
    const fresh = await browser.newContext({ viewport: { width: 1440, height: 900 }, reducedMotion: report.reduced_motion, storageState: { cookies: await context.cookies(), origins: [] } });
    fresh.setDefaultTimeout(15000);
    let page;
    let releaseResponse;
    const commits = [];
    async function readSaved(predicate) {
        const deadline = Date.now() + 20000;
        let document;
        do {
            document = await read(await fresh.request.get(`${path}/my-document`));
            if (predicate(document.source_document)) return document;
            await new Promise(resolve => setTimeout(resolve, 100));
        } while (Date.now() < deadline);
        assert.fail(`原版自动保存未达到预期状态: ${JSON.stringify({ document, commits, failed_routes: report.failed_routes })}`);
    }
    async function insertFromPicker() {
        await page.locator("[data-canvas-viewport]").click({ button: "right", position: { x: 550, y: 250 } });
        await page.getByRole("button", { name: "从素材库插入", exact: true }).click();
        const picker = page.locator(".asset-library-picker-modal");
        await picker.waitFor({ state: "visible" });
        await picker.locator(".asset-picker-card-action").click();
        await picker.getByRole("button", { name: "插入已选素材（1）", exact: true }).click();
        await picker.waitFor({ state: "hidden" });
    }
    async function readLive() {
        return page.evaluate(async key => {
            const { useCanvasStore } = await import("/canvas-app/src/stores/canvas/use-canvas-store.ts");
            return structuredClone(useCanvasStore.getState().projects.find(item => item.id === key));
        }, initial.source_key);
    }
    try {
        page = await fresh.newPage();
        page.on("pageerror", error => report.page_errors.push(error.message));
        page.on("response", recordFailure);
        page.on("request", request => {
            if (request.url() === `${path}/commits`) commits.push(request.postDataJSON());
        });
        report.normalization_step = "insert from source picker";
        await page.goto(`${origin}/canvas-app/canvas/${initial.source_key}`);
        await page.locator("[data-canvas-viewport]").waitFor();
        const normalized = page.waitForResponse(response => new URL(response.url()).pathname === "/api/v1/canvas-runtime/resources/normalize" && response.request().postDataJSON()?.resource_ids.includes(resource.id));
        await insertFromPicker();
        const placement = await read(await normalized);
        const copyId = placement.resource_map[resource.id];
        assert.ok(copyId && copyId !== resource.id, "cross-project insertion must own an independent file");
        assert.ok(placement.resource_aliases[copyId].includes(resource.id));
        const firstSave = await readSaved(document => document.nodes.length === 1 && document.nodes[0].metadata.storageKey === `resource:${copyId}`);
        const first = firstSave.source_document.nodes[0];
        assert.equal(first.metadata.assetId, saved.metadata.assetId);
        assert.equal((await readLive()).nodes[0].metadata.storageKey, `resource:${copyId}`);
        const copiedFile = await fresh.request.get(`${origin}/api/v1/canvas-runtime/resources/${copyId}/file`);
        const originalFile = await fresh.request.get(`${origin}/api/v1/canvas-runtime/resources/${resource.id}/file`);
        assert.ok(copiedFile.ok() && originalFile.ok());
        assert.deepEqual(await copiedFile.body(), await originalFile.body());
        report.source_picker_normalized = true;

        // Delay a real backend response, without inventing resource or commit data.
        // Continue editing through the source pointer/keyboard handlers while it waits.
        report.normalization_step = "edit while real normalization response is delayed";
        const gate = new Promise(resolve => { releaseResponse = resolve; });
        let receivedResponse;
        const received = new Promise(resolve => { receivedResponse = resolve; });
        await page.route("**/api/v1/canvas-runtime/resources/normalize", async route => {
            const response = await route.fetch();
            receivedResponse(await response.json());
            await gate;
            await route.fulfill({ response });
        }, { times: 1 });
        // Place the second insert away from the first node before opening its menu.
        await page.locator("[data-canvas-viewport]").click({ button: "right", position: { x: 850, y: 450 } });
        await page.getByRole("button", { name: "从素材库插入", exact: true }).click();
        const picker = page.locator(".asset-library-picker-modal");
        await picker.locator(".asset-picker-card-action").click();
        await picker.getByRole("button", { name: "插入已选素材（1）", exact: true }).click();
        await picker.waitFor({ state: "hidden" });
        const delayed = await received;
        assert.equal(delayed.resource_map[resource.id], copyId, "repeated insertion must reuse the durable normalization identity");
        const before = await readLive();
        assert.equal(before.nodes.length, 2);
        const second = before.nodes.find(node => node.id !== first.id);
        const element = page.locator(`[data-node-id="${second.id}"]`);
        const box = await element.boundingBox();
        await page.mouse.move(box.x + 20, box.y + 8);
        await page.mouse.down();
        await page.mouse.move(box.x + 100, box.y + 65, { steps: 12 });
        await page.mouse.up();
        const moved = (await readLive()).nodes.find(node => node.id === second.id);
        assert.notDeepEqual(moved.position, second.position);
        // The unchanged source groups history edits in a 180 ms debounce.
        await page.waitForTimeout(220);
        const removed = page.locator(`[data-node-id="${first.id}"]`);
        await removed.click({ position: { x: 20, y: 8 } });
        await page.keyboard.press("Delete");
        await removed.waitFor({ state: "detached" });
        await page.waitForTimeout(220);
        await page.keyboard.press("Control+z");
        await removed.waitFor({ state: "visible" });
        await page.locator("[data-canvas-viewport]").click({ button: "right", position: { x: 550, y: 550 } });
        assert.equal(await page.getByRole("button", { name: /^重做/ }).isEnabled(), true, "source redo must be available before the delayed response");
        await page.keyboard.press("Escape");
        releaseResponse();
        await readSaved(document => document.nodes.length === 2 && document.nodes.every(node => node.metadata.storageKey === `resource:${copyId}`));
        await page.locator("[data-canvas-viewport]").click({ button: "right", position: { x: 550, y: 550 } });
        const redo = page.getByRole("button", { name: /^重做/ });
        assert.equal(await redo.isEnabled(), true, "resource normalization must preserve the source redo stack");
        await redo.click();
        await removed.waitFor({ state: "detached" });
        const final = await readSaved(document => document.nodes.length === 1 && document.nodes[0].id === second.id && document.nodes[0].metadata.storageKey === `resource:${copyId}` && JSON.stringify(document.nodes[0].position) === JSON.stringify(moved.position));
        assert.equal(final.source_document.nodes[0].metadata.assetId, saved.metadata.assetId);
        const live = await readLive();
        assert.deepEqual(live.nodes.map(node => node.id), [second.id]);
        assert.deepEqual(live.nodes[0].position, moved.position);
        assert.equal(live.nodes[0].metadata.storageKey, `resource:${copyId}`);
        assert.equal(await page.locator(`[data-node-id="${first.id}"]`).count(), 0);
        assert.ok(commits.every(commit => commit.source_document.nodes.every(node => node.metadata.storageKey !== `resource:${resource.id}`)), "raw cross-project locators must never enter the immutable commit journal");
        report.normalization_preserves_live_drag_and_delete = true;
        report.normalization_preserves_undo_redo = true;
        report.normalization_delay = "real HTTP response held by Playwright; not a real network outage";

        report.normalization_step = "fresh browser reload";
        const clean = await browser.newContext({ viewport: { width: 1440, height: 900 }, reducedMotion: report.reduced_motion, storageState: { cookies: await context.cookies(), origins: [] } });
        try {
            const restored = await clean.newPage();
            restored.on("pageerror", error => report.page_errors.push(error.message));
            restored.on("response", recordFailure);
            await restored.goto(`${origin}/canvas-app/canvas/${initial.source_key}`);
            await restored.locator(`[data-node-id="${second.id}"]`).waitFor({ state: "visible" });
            await restored.waitForFunction(id => {
                const image = document.querySelector(`[data-node-id="${id}"] img`);
                return image instanceof HTMLImageElement && image.complete && image.naturalWidth === 37 && image.naturalHeight === 19;
            }, second.id);
            const reread = await read(await clean.request.get(`${path}/my-document`));
            assert.deepEqual(reread.source_document.nodes, final.source_document.nodes);
            const library = await read(await clean.request.get(`${origin}/api/v1/canvas-runtime/assets`));
            assert.deepEqual(library.assets.map(asset => asset.id), [saved.metadata.assetId]);
            const asset = (await read(await clean.request.get(`${origin}/api/v1/canvas-runtime/assets/${saved.metadata.assetId}`))).asset;
            assert.equal(asset.data.storageKey, `resource:${resource.id}`);
            report.source_picker_fresh_context_restored = true;
            report.source_picker_original_asset_preserved = true;
        } finally {
            await clean.close();
        }
        report.normalization_step = "complete";
    } catch (error) {
        if (page) await writeFile(resolve(".runtime/resource-python-browser/normalization-trace.json"), await page.evaluate(() => localStorage.getItem("beeftv:canvas-graph-trace:v1") || "[]")).catch(() => {});
        if (page) await page.screenshot({ path: resolve(".runtime/resource-python-browser/normalization-failure.png") }).catch(() => {});
        if (page) await writeFile(resolve(".runtime/resource-python-browser/normalization-failure-dom.txt"), await page.locator("body").ariaSnapshot()).catch(() => {});
        throw error;
    } finally {
        releaseResponse?.();
        await fresh.close();
    }
}
