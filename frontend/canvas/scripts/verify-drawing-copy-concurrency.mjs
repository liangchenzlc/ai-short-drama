import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { writeFile } from "node:fs/promises";
import { resolve } from "node:path";

export async function verifyConcurrentDrawingCopy({ context, origin, key, node, drawingPath, read, graph, openCanvas, stroke, save, observe, report, output }) {
    report.step = "copy freezes strokes and derived images while another editor saves";
    const cookies = await context.cookies();
    const editorContext = await context.browser().newContext({ viewport: { width: 1440, height: 900 }, storageState: { cookies, origins: [] }, permissions: ["clipboard-read", "clipboard-write"] });
    const copyContext = await context.browser().newContext({ viewport: { width: 1440, height: 900 }, storageState: { cookies, origins: [] }, permissions: ["clipboard-read", "clipboard-write"] });
    editorContext.setDefaultTimeout(20000);
    copyContext.setDefaultTimeout(20000);
    const editor = await editorContext.newPage();
    const target = await copyContext.newPage();
    try {
        observe(editor);
        observe(target);
        await openCanvas(target, key);
        await target.locator(`[data-node-id="${node.id}"]`).click({ button: "right" });
        await target.getByRole("button", { name: /^复制节点/ }).click();
        await target.waitForFunction(async () => (await navigator.clipboard.readText()).startsWith("open-ai-canvas-nodes:"));
        await openCanvas(editor, key);
        await editor.locator(`[data-node-id="${node.id}"]`).click({ button: "right" });
        await editor.getByRole("button", { name: "打开绘图", exact: true }).click();
        await editor.getByRole("button", { name: "保存绘图", exact: true }).waitFor();
        await editor.waitForFunction(() => [...document.querySelectorAll("button")].some(button => button.textContent === "保存绘图" && !button.disabled));
        const captured = (await read(await context.request.get(`${origin}${drawingPath}`))).drawing;
        await stroke(editor, 120);
        const destination = await read(await context.request.post(`${origin}/api/v1/projects`, {
            headers: { "Idempotency-Key": "drawing-copy-concurrent-source" },
            data: { name: "来源并发编辑复制", aspect: "16:9", workspace_mode: "infinite_canvas" },
        }));
        const destinationPath = `/api/v1/projects/${destination.id}/canvases/${destination.primary_canvas_id}`;
        const initial = await read(await context.request.get(`${origin}${destinationPath}/my-document`));
        await openCanvas(target, initial.source_key);
        let reads = 0;
        let release;
        const savedElsewhere = new Promise(resolve => { release = resolve; });
        let updated;
        await target.route(`${origin}${drawingPath}`, async route => {
            if (route.request().method() !== "GET") return route.continue();
            reads += 1;
            if (reads === 1) {
                const response = await route.fetch();
                assert.equal(response.status(), 200, await response.text());
                assert.equal((await response.json()).drawing.revision, captured.revision);
                try { updated = (await save(editor, drawingPath)).drawing; }
                finally { release(); }
                return route.fulfill({ response });
            }
            await savedElsewhere;
            return route.continue();
        });
        await target.locator("[data-canvas-viewport]").click({ position: { x: 500, y: 600 } });
        await target.keyboard.press("Control+v");
        const pasted = await graph(context, destinationPath, document => document.nodes.length === 1 && document.nodes[0].metadata.drawingRevision !== "0");
        const copiedKey = pasted.nodes[0].metadata.drawingId;
        const copy = (await read(await context.request.get(`${origin}/api/v1/canvas-runtime/canvas-projects/${initial.source_key}/drawings/${copiedKey}`))).drawing;
        report.concurrent_copy_source_reads = reads;
        report.concurrent_copy_versions = { captured: captured.revision, later: updated.revision, copiedShapes: copy.shapeCount, capturedShapes: captured.shapeCount };
        await writeFile(resolve(output, "copy-concurrency-capture.json"), JSON.stringify(report.concurrent_copy_versions, null, 2));
        assert.equal(updated.shapeCount, captured.shapeCount + 1);
        assert.deepEqual(copy.snapshot, captured.snapshot, "复制必须固定首次捕获的笔画版本");
        const bytes = async id => {
            const response = await context.request.get(`${origin}/api/v1/canvas-runtime/resources/${id}/file`);
            assert.equal(response.status(), 200);
            return response.body();
        };
        for (const [name, actualId, expectedId] of [["preview", copy.previewResourceId, captured.previewResourceId], ["render", copy.render.resourceId, captured.render.resourceId]]) {
            const [actual, expected] = await Promise.all([bytes(actualId), bytes(expectedId)]);
            const digest = value => createHash("sha256").update(value).digest("hex");
            const evidence = { actual: digest(actual), expected: digest(expected), actualBytes: actual.length, expectedBytes: expected.length };
            report[`concurrent_copy_${name}`] = evidence;
            assert.ok(actual.equals(expected), `${name} 必须与首次捕获版本逐字节一致：${JSON.stringify(evidence)}`);
        }
        assert.notEqual(copy.previewResourceId, captured.previewResourceId);
        assert.notEqual(copy.render.resourceId, captured.render.resourceId);
        assert.deepEqual((await read(await context.request.get(`${origin}${drawingPath}`))).drawing.snapshot, updated.snapshot);
        report.concurrent_node_copy_retains_one_drawing_version = true;
    } catch (error) {
        for (const [name, page] of [["editor", editor], ["destination", target]]) {
            await page.screenshot({ path: resolve(output, `copy-concurrency-${name}.png`) }).catch(() => {});
            await writeFile(resolve(output, `copy-concurrency-${name}.txt`), await page.locator("body").ariaSnapshot()).catch(() => {});
        }
        throw error;
    } finally {
        await copyContext.close();
        await editorContext.close();
    }
}
