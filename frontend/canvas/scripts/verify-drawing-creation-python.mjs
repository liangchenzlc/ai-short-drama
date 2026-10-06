import assert from "node:assert/strict";

export async function verifyDrawingCreation({ page, context, origin, path, key, projectId, read, graph, openCanvas, openDrawing, observe, report }) {
    report.step = "drawing project library copy";
    await openCanvas(page, key);
    const source = (await read(await context.request.get(`${origin}${path}/my-document`))).source_document;
    const title = "绘图整项目复制来源";
    await page.getByRole("button", { name: source.title, exact: true }).click();
    await page.getByRole("textbox", { name: "画布名称", exact: true }).fill(title);
    await page.getByRole("textbox", { name: "画布名称", exact: true }).press("Enter");
    await graph(context, path, document => document.title === title);
    const sources = (await read(await context.request.get(`${origin}/api/v1/projects/${projectId}/canvases`))).items;
    assert.equal(sources.length, 2);
    const creations = [];
    const receive = response => {
        const pathname = new URL(response.url()).pathname;
        if (response.request().method() === "POST" && (pathname === "/api/v1/canvas-workspace" || /^\/api\/v1\/projects\/\d+\/canvases$/.test(pathname))) {
            creations.push(read(response).then(result => ({ result, request: response.request().postDataJSON() })));
        }
    };
    page.on("response", receive);
    let copied;
    try {
        await page.goto(`${origin}/canvas-app/canvas`);
        await page.getByRole("button", { name: `${title} 画布操作`, exact: true }).click();
        const created = page.waitForResponse(response => new URL(response.url()).pathname === "/api/v1/canvas-workspace" && response.request().method() === "POST");
        await page.getByRole("menuitem", { name: "创建副本", exact: true }).click();
        copied = await read(await created);
        await page.waitForURL(`**/canvas/${copied.source_key}`);
        const group = await Promise.all(creations);
        assert.equal(group.length, sources.length);
        for (const { result, request } of group) {
            assert.equal(result.project_id, copied.project_id);
            assert.notEqual(result.project_id, projectId);
            await verifyCreation(result, request, true);
        }
        report.drawing_project_group_copied_atomically = true;
    } finally {
        page.off("response", receive);
    }
    report.step = "drawing top-bar canvas copy";
    await page.getByRole("button", { name: "切换画布", exact: true }).click();
    const row = page.locator(".canvas-topbar-canvas-menu-row.is-current");
    await row.hover({ position: { x: 20, y: 20 } });
    await row.getByRole("button", { name: /^画布操作：/ }).hover();
    const extra = page.waitForResponse(response => new URL(response.url()).pathname === `/api/v1/projects/${copied.project_id}/canvases` && response.request().method() === "POST");
    await page.getByRole("menuitem", { name: "复制画布", exact: true }).click();
    const response = await extra;
    const sibling = await read(response);
    assert.equal(sibling.project_id, copied.project_id);
    assert.notEqual(sibling.id, copied.id);
    await verifyCreation(sibling, response.request().postDataJSON(), false);
    report.drawing_whole_canvas_copied = true;

    report.step = "drawing readonly project copy";
    await page.goto(`${origin}/canvas-app/canvas/${key}?readonly=1`);
    const readonlyCopy = page.waitForResponse(response => new URL(response.url()).pathname === "/api/v1/canvas-workspace" && response.request().method() === "POST");
    await page.getByRole("button", { name: "复制项目", exact: true }).click();
    const readonlyResponse = await readonlyCopy;
    const readonly = await read(readonlyResponse);
    assert.notEqual(readonly.project_id, projectId);
    await page.waitForURL(`**/canvas/${readonly.source_key}`);
    await verifyCreation(readonly, readonlyResponse.request().postDataJSON(), true);
    report.drawing_readonly_project_copied = true;

    const clean = await context.browser().newContext({ viewport: { width: 1440, height: 900 }, storageState: { cookies: await context.cookies(), origins: [] } });
    clean.setDefaultTimeout(15000);
    try {
        const restored = await clean.newPage();
        observe(restored);
        await openCanvas(restored, readonly.source_key);
        const document = (await read(await clean.request.get(`${origin}/api/v1/projects/${readonly.project_id}/canvases/${readonly.id}/my-document`))).source_document;
        await openDrawing(restored, document.nodes[0].id);
        const loaded = await restored.evaluate(async ({ key, id }) => {
            const { loadCanvasDrawing } = await import("/canvas-app/src/lib/canvas/canvas-drawing-storage.ts");
            return loadCanvasDrawing(key, id);
        }, { key: readonly.source_key, id: document.nodes[0].metadata.drawingId });
        assert.deepEqual(loaded.snapshot, readonlyResponse.request().postDataJSON().drawing_documents[0].snapshot);
        assert.equal(loaded.origin, "canonical");
        report.drawing_project_copy_fresh_context_restored = true;
    } finally {
        await clean.close();
    }

    async function verifyCreation(result, request, copiedMedia) {
        assert.equal(request.drawing_documents.length, 1);
        assert.equal("initialDrawingDocuments" in request.source_document, false);
        const seed = request.drawing_documents[0];
        assert.equal(seed.revision, "0");
        const drawing = (await read(await context.request.get(`${origin}/api/v1/canvas-runtime/canvas-projects/${result.source_key}/drawings/${seed.drawingId}`))).drawing;
        assert.deepEqual(drawing.snapshot, seed.snapshot);
        assert.equal(drawing.revision, "1");
        assert.equal(drawing.previewResourceId, result.resource_map[seed.previewResourceId]);
        assert.equal(drawing.render.resourceId, result.resource_map[seed.render.resourceId]);
        if (copiedMedia) assert.notEqual(drawing.previewResourceId, seed.previewResourceId);
        else assert.equal(drawing.previewResourceId, seed.previewResourceId);
        for (const [before, after] of [[seed.previewResourceId, drawing.previewResourceId], [seed.render.resourceId, drawing.render.resourceId]]) {
            assert.deepEqual(await (await context.request.get(`${origin}/api/v1/canvas-runtime/resources/${after}/file`)).body(), await (await context.request.get(`${origin}/api/v1/canvas-runtime/resources/${before}/file`)).body());
        }
        const graph = (await read(await context.request.get(`${origin}/api/v1/projects/${result.project_id}/canvases/${result.id}/my-document`))).source_document;
        assert.equal(graph.nodes[0].metadata.drawingRevision, "1");
        assert.equal(graph.nodes[0].metadata.drawingShapeCount, drawing.shapeCount);
        assert.equal("initialDrawingDocuments" in graph, false);
    }
}
