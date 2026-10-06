import assert from "node:assert/strict";

/** Real menu, durable browser storage and Python storage; only delivery failures are injected. */
export async function verifyCreationRecovery({ browser, context, origin, read, saved, report, recordFailure }) {
    await verifyGroupPreparationRecovery({ browser, context, origin, read, report, recordFailure });
    await verifyGroupRecovery({ browser, context, origin, read, report, recordFailure });
    for (const delivery of ["before-send", "after-commit"]) {
        const fresh = await browser.newContext({ viewport: { width: 1440, height: 900 }, reducedMotion: report.reduced_motion, storageState: { cookies: await context.cookies(), origins: [] } });
        fresh.setDefaultTimeout(15000);
        try {
            const page = await fresh.newPage();
            page.on("pageerror", error => report.page_errors.push(error.message));
            page.on("response", recordFailure);
            report.creation_recovery_step = `${delivery}: original copy menu`;
            await page.goto(`${origin}/canvas-app/canvas`);
            const attempts = [];
            let committed;
            await page.route(`${origin}/api/v1/canvas-workspace`, async route => {
                if (route.request().method() !== "POST") return route.continue();
                attempts.push({ key: route.request().headers()["idempotency-key"], body: route.request().postDataJSON() });
                if (delivery === "after-commit") committed = await read(await route.fetch());
                await route.fulfill({ status: 503, contentType: "application/json", json: { error: { code: "test_creation_delivery", message: "测试注入：创建回执未收到，原请求可重试" } } });
            });
            await page.getByRole("button", { name: "完整画布复制来源 画布操作", exact: true }).click();
            const failed = page.waitForResponse(response => response.request().method() === "POST" && new URL(response.url()).pathname === "/api/v1/canvas-workspace" && response.status() === 503);
            await page.getByRole("menuitem", { name: "创建副本", exact: true }).click();
            await failed;
            const frozen = structuredClone(attempts[0]);
            if (delivery === "after-commit") {
                // Model editing after the frozen request. The real reload and
                // server commit below must preserve it without changing that request.
                await page.evaluate(async key => {
                    const { useCanvasStore } = await import("/canvas-app/src/stores/canvas/use-canvas-store.ts");
                    const state = useCanvasStore.getState();
                    const current = state.projects.find(item => item.id === key);
                    state.updateProject(key, {
                        title: "回执丢失后的新编辑",
                        nodes: current.nodes.map(node => ({ ...node, position: { ...node.position, x: node.position.x + 73 } })),
                    });
                }, frozen.body.source_key);
            }
            await page.evaluate(async () => {
                const { flushCanvasStorePersistence } = await import("/canvas-app/src/stores/canvas/use-canvas-store.ts");
                await flushCanvasStorePersistence();
            });
            await page.unroute(`${origin}/api/v1/canvas-workspace`);
            report.creation_recovery_step = `${delivery}: reopen durable draft`;
            const accepted = page.waitForResponse(response => response.request().method() === "POST" && new URL(response.url()).pathname === "/api/v1/canvas-workspace" && response.ok());
            await page.goto(`${origin}/canvas-app/canvas/${frozen.body.source_key}`);
            const response = await accepted;
            const recovered = await read(response);
            assert.equal(response.request().headers()["idempotency-key"], frozen.key);
            assert.deepEqual(response.request().postDataJSON(), frozen.body);
            if (committed) assert.deepEqual(recovered, committed);
            await page.locator(`[data-node-id="${saved.id}"]`).waitFor({ state: "visible" });
            const state = await page.evaluate(async key => {
                const { useCanvasStore } = await import("/canvas-app/src/stores/canvas/use-canvas-store.ts");
                const { pendingExternalCanvasRevision } = await import("/canvas-app/src/services/local-workspace-repository.ts");
                return { project: useCanvasStore.getState().projects.find(item => item.id === key), conflict: Boolean(pendingExternalCanvasRevision(key)) };
            }, recovered.source_key);
            assert.equal(state.conflict, false, "the acknowledged resource remap is not an external edit");
            assert.equal(state.project.workspaceProjectId, recovered.project_id);
            assert.equal(state.project.nodes[0].metadata.storageKey, `resource:${Object.values(recovered.resource_map)[0]}`);
            const remote = await read(await fresh.request.get(`${origin}/api/v1/projects/${recovered.project_id}/canvases/${recovered.id}/my-document`));
            assert.deepEqual(state.project.nodes, remote.source_document.nodes);
            if (delivery === "after-commit") {
                assert.equal(state.project.title, "回执丢失后的新编辑");
                assert.equal(state.project.nodes[0].position.x, frozen.body.source_document.nodes[0].position.x + 73);
                report.creation_recovery_keeps_later_edits = true;
            }
            const group = await read(await fresh.request.get(`${origin}/api/v1/projects/${recovered.project_id}/canvases`));
            assert.equal(group.items.length, 1);
            report[`creation_recovery_${delivery.replaceAll("-", "_")}`] = true;
            if (delivery === "before-send") {
                report.creation_recovery_step = "archived canvas: never create from a missing GET";
                const csrf = (await fresh.cookies()).find(cookie => cookie.name === "sd_csrf");
                const deleted = await fresh.request.delete(`${origin}/api/v1/projects/${recovered.project_id}/canvases/${recovered.id}`, {
                    headers: { Origin: origin, "X-CSRF-Token": csrf.value, "Idempotency-Key": `recovery-archive:${recovered.id}` },
                    data: { expected_row_version: remote.row_version },
                });
                assert.ok(deleted.ok(), await deleted.text());
                const unexpected = [];
                page.on("request", request => {
                    if (request.method() === "POST" && new URL(request.url()).pathname === "/api/v1/canvas-workspace") unexpected.push(request.postDataJSON());
                });
                const missing = page.waitForResponse(response => new URL(response.url()).pathname === `/api/v1/canvas-workspace/resolve/${recovered.source_key}` && response.status() === 404);
                await page.reload();
                await missing;
                const allowed = await page.evaluate(async key => {
                    const { hostCanvasAccess } = await import("/canvas-app/src/services/host-canvas-access.ts");
                    const { captureUserScope } = await import("/canvas-app/src/lib/user-scope-guard.ts");
                    const { syncLocalCanvasProjectToBackend } = await import("/canvas-app/src/services/local-workspace-repository.ts");
                    const { readInitialCanvasWrite } = await import("/canvas-app/src/services/canvas-initial-write.ts");
                    const { localForageStorageForScope } = await import("/canvas-app/src/lib/localforage-storage.ts");
                    const scope = captureUserScope();
                    await syncLocalCanvasProjectToBackend(key).catch(() => {});
                    return { denial: hostCanvasAccess.reason(scope, key), initial: await readInitialCanvasWrite(localForageStorageForScope(scope.userScope), key) };
                }, recovered.source_key);
                assert.ok(allowed.denial);
                assert.equal(allowed.initial, null);
                assert.deepEqual(unexpected, []);
                report.creation_missing_get_never_recreates = true;
            }
        } finally {
            await fresh.close();
        }
    }
    report.creation_recovery_step = "complete";
    report.creation_recovery_fault = "Playwright 503 before delivery and after real successful POST; retry and media use Python/MySQL/MinIO";
}

async function verifyGroupPreparationRecovery(input) {
    for (const fault of ["second-initial", "graph-cache"]) {
        await verifyGroupPreparationFault({ ...input, fault });
    }
}

async function verifyGroupPreparationFault({ browser, context, origin, read, report, recordFailure, fault }) {
    const fresh = await browser.newContext({ viewport: { width: 1440, height: 900 }, storageState: { cookies: await context.cookies(), origins: [] } });
    fresh.setDefaultTimeout(15000);
    try {
        const page = await fresh.newPage();
        page.on("pageerror", error => report.page_errors.push(error.message));
        page.on("response", recordFailure);
        report.creation_recovery_step = `group preparation: ${fault} write fails`;
        await page.goto(`${origin}/canvas-app/canvas`);
        await page.getByRole("button", { name: "完整画布复制来源 副本 画布操作", exact: true }).waitFor({ state: "visible" });
        const sent = [];
        page.on("request", request => {
            if (request.method() === "POST" && new URL(request.url()).pathname === "/api/v1/canvas-workspace") sent.push(request.postDataJSON());
        });
        await page.evaluate(async fault => {
            const { CANVAS_STORE_KEY } = await import("/canvas-app/src/stores/canvas/use-canvas-store.ts");
            const { scopedStorageKey } = await import("/canvas-app/src/lib/user-scope.ts");
            const cacheKey = scopedStorageKey(CANVAS_STORE_KEY);
            const original = IDBObjectStore.prototype.put;
            let count = 0;
            IDBObjectStore.prototype.put = function (...args) {
                const fail = fault === "graph-cache" ? args[1] === cacheKey
                    : String(args[1]).startsWith("canvas-initial-write:") && ++count === 2;
                if (this.name === "app_state" && fail) {
                    window.__creationStorageFailure = true;
                    throw new DOMException(`测试注入：${fault} 写入失败`, "QuotaExceededError");
                }
                const result = Reflect.apply(original, this, args);
                if (this.name === "app_state" && String(args[1]).startsWith("canvas-initial-group:")) {
                    this.transaction.addEventListener("complete", () => { window.__creationGroupPersisted = true; }, { once: true });
                }
                return result;
            };
        }, fault);
        await page.getByRole("button", { name: "完整画布复制来源 副本 画布操作", exact: true }).click();
        await page.getByRole("menuitem", { name: "创建副本", exact: true }).click();
        await page.waitForFunction(() => window.__creationStorageFailure === true);
        if (fault === "graph-cache") await page.waitForFunction(() => window.__creationGroupPersisted === true);
        const drafts = await page.evaluate(async () => {
            const { useCanvasStore } = await import("/canvas-app/src/stores/canvas/use-canvas-store.ts");
            return structuredClone(useCanvasStore.getState().projects.filter(item => item.revision === "0"));
        });
        assert.equal(drafts.length, 2);
        assert.deepEqual(sent, [], "no remote creation is allowed until all initial requests are durable");
        const root = drafts.find(item => item.workspaceProjectId === item.id);
        const child = drafts.find(item => item.id !== root.id);
        report.creation_recovery_step = "group preparation: reload from partial local preparation";
        const rootSaved = page.waitForResponse(response => response.request().method() === "POST" && new URL(response.url()).pathname === "/api/v1/canvas-workspace" && response.ok());
        const childSaved = page.waitForResponse(response => response.request().method() === "POST" && /^\/api\/v1\/projects\/\d+\/canvases$/.test(new URL(response.url()).pathname) && response.request().postDataJSON()?.source_key === child.id && response.ok());
        await page.reload();
        const copied = await read(await rootSaved);
        assert.equal(copied.source_key, root.id);
        const sibling = await read(await childSaved);
        assert.equal(sibling.project_id, copied.project_id);
        const group = await read(await fresh.request.get(`${origin}/api/v1/projects/${copied.project_id}/canvases`));
        assert.deepEqual(new Set(group.items.map(item => item.source_key)), new Set(drafts.map(item => item.id)));
        report[fault === "graph-cache" ? "creation_group_missing_cache_recovered" : "creation_group_partial_storage_recovered"] = true;
        report.creation_storage_fault = "Native IndexedDB put throws QuotaExceededError on the second per-canvas initial request or the ordinary graph cache; real menu and reload, real successful API writes";
    } finally {
        await fresh.close();
    }
}

async function verifyGroupRecovery({ browser, context, origin, read, report, recordFailure }) {
    const fresh = await browser.newContext({ viewport: { width: 1440, height: 900 }, storageState: { cookies: await context.cookies(), origins: [] } });
    fresh.setDefaultTimeout(15000);
    try {
        const page = await fresh.newPage();
        page.on("pageerror", error => report.page_errors.push(error.message));
        page.on("response", recordFailure);
        report.creation_recovery_step = "group: first canvas ACK lost";
        await page.goto(`${origin}/canvas-app/canvas`);
        let frozen;
        let root;
        await page.route(`${origin}/api/v1/canvas-workspace`, async route => {
            if (route.request().method() !== "POST") return route.continue();
            frozen = { key: route.request().headers()["idempotency-key"], body: route.request().postDataJSON() };
            root = await read(await route.fetch());
            await route.fulfill({ status: 503, contentType: "application/json", json: { error: { code: "test_group_delivery", message: "测试注入：多画布复制中断" } } });
        });
        await page.getByRole("button", { name: "完整画布复制来源 副本 画布操作", exact: true }).click();
        const failed = page.waitForResponse(response => response.request().method() === "POST" && new URL(response.url()).pathname === "/api/v1/canvas-workspace" && response.status() === 503);
        await page.getByRole("menuitem", { name: "创建副本", exact: true }).click();
        await failed;
        const drafts = await page.evaluate(async key => {
            const { useCanvasStore, flushCanvasStorePersistence } = await import("/canvas-app/src/stores/canvas/use-canvas-store.ts");
            const { readInitialCanvasWrite } = await import("/canvas-app/src/services/canvas-initial-write.ts");
            const { localForageStorageForScope } = await import("/canvas-app/src/lib/localforage-storage.ts");
            const { getActiveUserScope } = await import("/canvas-app/src/lib/user-scope.ts");
            await flushCanvasStorePersistence();
            const group = useCanvasStore.getState().projects.filter(item => item.workspaceProjectId === key);
            const storage = localForageStorageForScope(getActiveUserScope());
            return Promise.all(group.map(item => readInitialCanvasWrite(storage, item.id)));
        }, root.source_key);
        assert.equal(drafts.length, 2, "freeze the whole copy operation before its first remote write");
        assert.ok(drafts.every(Boolean));
        const child = drafts.find(item => item.id !== root.source_key);
        await page.unroute(`${origin}/api/v1/canvas-workspace`);
        report.creation_recovery_step = "group: reload and resume same project";
        const replayed = page.waitForResponse(response => response.request().method() === "POST" && new URL(response.url()).pathname === "/api/v1/canvas-workspace" && response.ok());
        const childSaved = page.waitForResponse(response => response.request().method() === "POST" && new URL(response.url()).pathname === `/api/v1/projects/${root.project_id}/canvases` && response.ok());
        await page.goto(`${origin}/canvas-app/canvas/${root.source_key}`);
        const replay = await replayed;
        assert.equal(replay.request().headers()["idempotency-key"], frozen.key);
        assert.deepEqual(replay.request().postDataJSON(), frozen.body);
        assert.deepEqual(await read(replay), root);
        const sibling = await read(await childSaved);
        assert.equal(sibling.source_key, child.id);
        const group = await read(await fresh.request.get(`${origin}/api/v1/projects/${root.project_id}/canvases`));
        assert.deepEqual(new Set(group.items.map(item => item.source_key)), new Set(drafts.map(item => item.id)));
        report.creation_group_recovery = true;
    } finally {
        await fresh.close();
    }
}
