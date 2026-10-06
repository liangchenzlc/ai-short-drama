import assert from "node:assert/strict";

async function holdCopy(page, origin, read, trigger) {
    let release;
    const gate = new Promise(resolve => { release = resolve; });
    let ready;
    let rejectReady;
    const prepared = new Promise((resolve, reject) => { ready = resolve; rejectReady = reject; });
    await page.route(`${origin}/api/v1/canvas-workspace`, async route => {
        if (route.request().method() !== "POST") return route.continue();
        try {
            const response = await route.fetch();
            const copied = await read(response);
            ready({ copied, body: route.request().postDataJSON(), key: route.request().headers()["idempotency-key"], release });
            await gate;
            await route.fulfill({ response });
        } catch (error) {
            rejectReady(error);
            throw error;
        }
    });
    if (trigger) await trigger();
    else {
        await page.getByRole("button", { name: "完整画布复制来源 画布操作", exact: true }).click();
        await page.getByRole("menuitem", { name: "创建副本", exact: true }).click();
    }
    return prepared;
}

async function frames(page) {
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
}

async function login(context, origin, read, username, password) {
    const csrf = (await context.cookies()).find(cookie => cookie.name === "sd_csrf");
    return read(await context.request.post(`${origin}/api/v1/auth/login`, { headers: { Origin: origin, "X-CSRF-Token": csrf.value }, data: { username, password } }));
}

export async function verifyCreationSession({ browser, context, origin, read, config, original, saved, report, recordFailure }) {
    for (const scenario of ["navigation", "account", "readonly"]) {
        const fresh = await browser.newContext({ viewport: { width: 1440, height: 900 }, storageState: { cookies: await context.cookies(), origins: [] } });
        fresh.setDefaultTimeout(15000);
        let held;
        try {
            const page = await fresh.newPage();
            page.on("pageerror", error => report.page_errors.push(error.message));
            page.on("response", recordFailure);
            report.creation_session_step = `${scenario}: original copy menu, hold real ACK`;
            await page.goto(`${origin}/canvas-app/canvas${scenario === "readonly" ? `/${original.source_key}?readonly=1` : ""}`);
            await page.evaluate(async () => {
                const { projectSyncProgress } = await import("/canvas-app/src/stores/use-sync-progress-store.ts");
                window.__copyProgress = projectSyncProgress;
            });
            held = await holdCopy(page, origin, read, scenario === "readonly" ? () => page.getByRole("button", { name: "复制项目", exact: true }).click() : undefined);
            const delivered = page.waitForResponse(response => response.request().method() === "POST" && new URL(response.url()).pathname === "/api/v1/canvas-workspace" && response.ok());
            if (scenario === "navigation") {
                await page.locator('[data-nav-id="assets"]').click();
                await page.waitForURL("**/assets");
                held.release();
                await delivered;
                await page.waitForFunction(key => window.__copyProgress(key)?.phase === "done", held.copied.source_key);
                await frames(page);
                assert.equal(new URL(page.url()).pathname, "/canvas-app/assets", "a completed background copy cannot undo the user's navigation");
                report.creation_navigation_preserved = true;
            } else if (scenario === "readonly") {
                held.release();
                await delivered;
                await page.waitForURL(`**/canvas/${held.copied.source_key}`);
                await page.locator(`[data-node-id="${saved.id}"]`).waitFor({ state: "visible" });
                assert.notEqual(held.copied.project_id, original.project_id);
                const remote = await read(await fresh.request.get(`${origin}/api/v1/projects/${held.copied.project_id}/canvases/${held.copied.id}/my-document`));
                assert.equal(remote.source_document.nodes[0].metadata.assetId, saved.metadata.assetId);
                assert.equal(remote.source_document.nodes[0].metadata.storageKey, `resource:${Object.values(held.copied.resource_map)[0]}`);
                report.creation_readonly_copy_persisted = true;
            } else {
                const other = await login(fresh, origin, read, config.otherUsername, config.password);
                const scope = await page.evaluate(async () => {
                    const { establishCanvasSession } = await import("/canvas-app/src/services/host-session.ts");
                    const { applyUserSession } = await import("/canvas-app/src/lib/user-session.ts");
                    const bootstrap = await establishCanvasSession();
                    await applyUserSession(bootstrap);
                    return bootstrap.workspace.id;
                });
                assert.equal(scope, other.user.id);
                held.release();
                await delivered;
                await frames(page);
                const state = await page.evaluate(async ({ key, otherScope }) => {
                    const { useCanvasStore } = await import("/canvas-app/src/stores/canvas/use-canvas-store.ts");
                    const { readInitialCanvasWrite } = await import("/canvas-app/src/services/canvas-initial-write.ts");
                    const { localForageStorageForScope } = await import("/canvas-app/src/lib/localforage-storage.ts");
                    return { projects: useCanvasStore.getState().projects.map(project => project.id), pending: await readInitialCanvasWrite(localForageStorageForScope(otherScope), key) };
                }, { key: held.copied.source_key, otherScope: scope });
                assert.deepEqual(state, { projects: [], pending: null });
                assert.equal(new URL(page.url()).pathname, "/canvas-app/canvas");
                const visible = await read(await fresh.request.get(`${origin}/api/v1/canvas-workspace`));
                assert.deepEqual(visible.items, []);
                assert.equal((await fresh.request.get(`${origin}/api/v1/canvas-workspace/resolve/${held.copied.source_key}`)).status(), 404);
                const resource = Object.values(held.copied.resource_map)[0];
                assert.equal((await fresh.request.get(`${origin}/api/v1/canvas-runtime/resources/${resource}/file`)).status(), 404);
                await page.unroute(`${origin}/api/v1/canvas-workspace`);
                await login(fresh, origin, read, config.username, config.password);
                const replay = page.waitForResponse(response => response.request().method() === "POST" && new URL(response.url()).pathname === "/api/v1/canvas-workspace" && response.ok());
                await page.reload();
                const response = await replay;
                assert.equal(response.request().headers()["idempotency-key"], held.key);
                assert.deepEqual(response.request().postDataJSON(), held.body);
                assert.deepEqual(await read(response), held.copied);
                report.creation_account_switch_isolated = true;
            }
        } finally {
            held?.release();
            await fresh.close();
        }
    }
    report.creation_session_step = "complete";
    report.creation_session_fixture = "real menu and navigation; real second-account authentication with explicit invocation of the canvas session bootstrap; real HTTP ACK held by Playwright";
}
