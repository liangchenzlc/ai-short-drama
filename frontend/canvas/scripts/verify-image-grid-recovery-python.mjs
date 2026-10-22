import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { chromium } from "playwright";
import { createServer } from "vite";
import ts from "typescript";

let input = "";
for await (const chunk of process.stdin) input += chunk;
const config = JSON.parse(input);
const origin = "http://127.0.0.1:4199";
const resources = "/api/v1/canvas-runtime/resources";
const path = `/api/v1/projects/${config.projectId}/canvases/${config.canvasId}`;
const commitPath = `${path}/commits`;
const childCount = config.tool === "grid" ? 4 : 1;
const targetLongEdge = config.targetLongEdge || 1024;
const outputHeight = ({ 1024: 526, 2048: 1052, 4096: 2103 })[targetLongEdge];
const output = resolve(config.outputDirectory);
const report = { step: "start", tool: config.tool, scenario: config.scenario, page_errors: [], uploads: 0, commits: [], failure_injections: [] };
await mkdir(output, { recursive: true });
const server = await createServer({
    server: { host: "127.0.0.1", port: 4199, strictPort: true, proxy: { "/api": { target: config.apiUrl, changeOrigin: true } } },
    logLevel: "silent",
});
await server.listen();
let browser;
let page;

async function read(response) {
    assert.ok(response.ok(), `HTTP ${response.status()} ${new URL(response.url()).pathname}: ${await response.text()}`);
    return response.json();
}

async function login(username) {
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, reducedMotion: "no-preference" });
    context.setDefaultTimeout(20000);
    context.setDefaultNavigationTimeout(60000);
    await read(await context.request.post(`${origin}/api/v1/auth/login`, {
        headers: { Origin: origin }, data: { username, password: config.password },
    }));
    const csrf = (await context.cookies()).find(cookie => cookie.name === "sd_csrf");
    assert.ok(csrf);
    await context.setExtraHTTPHeaders({ Origin: origin, "X-CSRF-Token": csrf.value });
    return context;
}

async function graph(context, predicate) {
    let current;
    for (let attempt = 0; attempt < 150; attempt++) {
        current = (await read(await context.request.get(`${origin}${path}/my-document`))).source_document;
        if (predicate(current)) return current;
        await new Promise(resolve => setTimeout(resolve, 100));
    }
    assert.fail(`宫格恢复未达到预期：${JSON.stringify(current)}`);
}

async function localGraph() {
    return page.evaluate(async key => {
        const { CANVAS_STORE_KEY, useCanvasStore } = await import("/canvas-app/src/stores/canvas/use-canvas-store.ts");
        const { localForageStorageForScope } = await import("/canvas-app/src/lib/localforage-storage.ts");
        const { getActiveUserScope } = await import("/canvas-app/src/lib/user-scope.ts");
        const saved = JSON.parse(await localForageStorageForScope(getActiveUserScope()).getItem(CANVAS_STORE_KEY));
        return {
            memory: useCanvasStore.getState().projects.find(project => project.id === key),
            cached: saved?.state?.projects?.find(project => project.id === key),
        };
    }, config.sourceKey);
}

async function draft() {
    for (let attempt = 0; attempt < 100; attempt++) {
        const result = await localGraph();
        if (result.memory?.nodes.length === childCount + 1 && result.cached?.nodes.length === childCount + 1 && result.cached?.connections.length === childCount) return result.memory;
        await new Promise(resolve => setTimeout(resolve, 100));
    }
    assert.fail("失败后的完整宫格未保存在本人内存和IndexedDB草稿中");
}

async function runTool(source) {
    const node = page.locator(`[data-node-id="${source.id}"]`);
    await node.click();
    await node.hover();
    await page.getByRole("button", { name: config.tool === "upscale" ? "工具" : "图片工具", exact: true }).click();
    if (config.tool === "upscale") {
        await page.getByRole("menuitem", { name: "调整尺寸", exact: true }).click();
        await page.getByRole("menu").waitFor({ state: "hidden" });
        const dialog = page.getByRole("dialog");
        await page.waitForFunction(() => [...document.querySelectorAll(".ant-modal, .ant-modal-mask")].every(element => getComputedStyle(element).animationName === "none"));
        await dialog.getByText(`${source.metadata.naturalWidth} x ${source.metadata.naturalHeight} px`, { exact: true }).first().waitFor();
        if (config.scenario === "max_source") return dialog;
        await dialog.getByText("1024 x 526 px", { exact: true }).waitFor();
        assert.equal(await dialog.getByRole("radio", { name: "高质量插值 适合照片和细节图", exact: true }).isChecked(), true);
        await dialog.getByText(({ high: "高质量插值", bilinear: "双线性", nearest: "最近邻" })[config.algorithm || "nearest"], { exact: true }).click();
        if (targetLongEdge !== 1024) {
            await dialog.getByText(`${targetLongEdge === 2048 ? "2K" : "4K"} · ${targetLongEdge}px`, { exact: true }).click();
            await dialog.getByText(`${targetLongEdge} x ${outputHeight} px`, { exact: true }).waitFor();
        }
        await page.waitForFunction(() => !document.querySelector(".ant-modal .ant-segmented-thumb"));
        if (config.scenario.startsWith("normal_")) return dialog;
        await dialog.getByRole("button", { name: "生成放大图", exact: true }).click();
        return;
    }
    if (config.tool !== "annotation") {
        await page.getByRole("menuitem", { name: "宫格切分", exact: true }).click();
        await page.getByRole("dialog", { name: "宫格切分", exact: true }).getByRole("button", { name: "4宫格 (2×2)", exact: true }).click();
        return;
    }
    await page.getByRole("menuitem", { name: "标注", exact: true }).click();
    const editor = node.locator("[data-image-annotation-inline]");
    const canvas = editor.getByLabel("图片标注画布", { exact: true });
    await page.waitForFunction(id => document.querySelector(`[data-node-id="${id}"] [data-image-annotation-inline] canvas`)?.width === 37, source.id);
    const box = await canvas.boundingBox();
    await page.mouse.move(box.x + box.width * 7 / 37, box.y + box.height * 9 / 19);
    await page.mouse.down();
    await page.mouse.move(box.x + box.width * 25 / 37, box.y + box.height * 9 / 19, { steps: 12 });
    await page.mouse.up();
    report.annotation_overlay = await canvas.evaluate(element => element.toDataURL("image/png"));
    await editor.getByRole("button", { name: "保存标注", exact: true }).click();
}

async function settle() {
    await page.evaluate(async () => {
        for (let index = 0; index < 8; index++) await new Promise(requestAnimationFrame);
        const { flushCanvasStorePersistence } = await import("/canvas-app/src/stores/canvas/use-canvas-store.ts");
        await flushCanvasStorePersistence();
    });
}

function pngHash(body) {
    const start = body.indexOf(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]));
    assert.ok(start >= 0, "真实资源上传应包含PNG字节");
    let offset = start + 8;
    while (offset + 12 <= body.length) {
        const size = body.readUInt32BE(offset);
        const type = body.toString("ascii", offset + 4, offset + 8);
        offset += size + 12;
        if (type === "IEND") return createHash("sha256").update(body.subarray(start, offset)).digest("hex");
    }
    assert.fail("上传PNG应有完整IEND");
}

try {
    await (async () => {
    browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH });
    const owner = await login(config.username);
    const member = await login(config.memberUsername);
    const peer = await login(config.username);
    page = await owner.newPage();
    page.on("pageerror", error => report.page_errors.push(error.message));
    page.on("request", request => {
        const route = new URL(request.url()).pathname;
        if (route === resources && request.method() === "POST") report.uploads++;
        if (route === commitPath && request.method() === "POST") report.commits.push({ key: request.headers()["idempotency-key"], body: request.postDataJSON() });
    });
    await page.goto(`${origin}/canvas-app/canvas/${config.sourceKey}`);
    await page.locator("[data-canvas-viewport]").waitFor({ state: "visible" });
    await page.evaluate(() => document.fonts.ready);
    report.step = "原上传与宫格故障注入";
    await page.locator('input[type="file"][accept^="image/"]').setInputFiles({
        name: "grid-recovery-pattern.png", mimeType: "image/png", buffer: Buffer.from(config.image, "base64"),
    });
    const original = await graph(owner, document => document.nodes.length === 1 && document.nodes[0].metadata?.assetId);
    const source = original.nodes[0];
    report.source_node_id = source.id;
    if (config.tool === "upscale") {
        const node = page.locator(`[data-node-id="${source.id}"]`);
        await node.click();
        await node.hover();
        await page.getByRole("button", { name: "图片工具", exact: true }).click();
        assert.equal(await page.getByRole("menuitem", { name: "调整尺寸", exact: true }).count(), 0);
        await page.keyboard.press("Escape");
        report.desktop_upscale_hidden = true;
        await page.setViewportSize({ width: 600, height: 900 });
        await page.getByRole("button", { name: "适合屏幕", exact: true }).click();
        await settle();
        if (config.scenario !== "max_source") {
            const upstreamJs = ts.transpileModule(config.upstreamImageSource, {
                compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
            }).outputText;
            const reference = await page.evaluate(async ({ moduleUrl, image, algorithm, targetLongEdge }) => {
                const pinned = await import(moduleUrl);
                return pinned.upscaleDataUrl(`data:image/png;base64,${image}`, { targetLongEdge, algorithm });
            }, { moduleUrl: `data:text/javascript;base64,${Buffer.from(upstreamJs).toString("base64")}`, image: config.image, algorithm: config.algorithm, targetLongEdge });
            const bytes = Buffer.from(reference.slice(reference.indexOf(",") + 1), "base64");
            await writeFile(resolve(output, "upstream-image.png"), bytes);
            report.upstream_commit = config.upstreamCommit;
            report.upstream_source_sha256 = createHash("sha256").update(config.upstreamImageSource).digest("hex");
            report.upstream_image_sha256 = createHash("sha256").update(bytes).digest("hex");
        }
    }
    const startingCommits = report.commits.length;
    if (config.scenario.startsWith("normal_") || config.scenario === "max_source") {
        report.step = "原调整尺寸默认值、取消与边界";
        let dialog = await runTool(source);
        if (config.scenario === "max_source") {
            await dialog.getByText("图片已达到 4K，无需放大", { exact: true }).waitFor();
            assert.equal(await dialog.getByRole("button", { name: "生成放大图", exact: true }).isEnabled(), false);
            for (const name of ["1K · 1024px", "2K · 2048px", "4K · 4096px"]) assert.equal(await dialog.getByRole("radio", { name, exact: true }).isDisabled(), true);
        } else {
            assert.equal(await dialog.getByRole("radio", { name: `${targetLongEdge === 1024 ? "1K" : targetLongEdge === 2048 ? "2K" : "4K"} · ${targetLongEdge}px`, exact: true }).isChecked(), true);
            await dialog.getByText("2K · 2048px", { exact: true }).click();
            await dialog.getByText("2048 x 1052 px", { exact: true }).waitFor();
            await dialog.getByText("4K · 4096px", { exact: true }).click();
            await dialog.getByText("4096 x 2103 px", { exact: true }).waitFor();
        }
        await page.keyboard.press("Escape");
        await dialog.waitFor({ state: "hidden" });
        await settle();
        const canceled = (await read(await owner.request.get(`${origin}${path}/my-document`))).source_document;
        assert.deepEqual(canceled.nodes, original.nodes);
        assert.deepEqual(canceled.connections, original.connections);
        assert.equal(report.uploads, 1);
        report.cancel_kept_source = true;
        if (config.scenario === "max_source") {
            report.max_source_disabled = true;
            report.final_nodes = original.nodes;
            report.step = "complete";
            return;
        }
        dialog = await runTool(source);
        await page.screenshot({ path: resolve(output, "upscale-dialog.png") });
        await dialog.getByRole("button", { name: "生成放大图", exact: true }).click();
        const final = await graph(owner, document => document.nodes.length === 2 && document.connections.length === 1 && document.nodes.every(node => node.metadata.assetId));
        assert.deepEqual(final.nodes.find(node => node.id === source.id), source);
        const child = final.nodes.find(node => node.id !== source.id);
        const bytes = await owner.request.get(`${origin}${resources}/${child.metadata.storageKey.slice(9)}/file`);
        assert.ok(bytes.ok());
        report.children = [{ id: child.id, title: child.title, storageKey: child.metadata.storageKey, sha256: createHash("sha256").update(await bytes.body()).digest("hex") }];
        report.final_nodes = final.nodes;
        assert.equal(report.uploads, 2);
        const cookies = (await owner.storageState()).cookies;
        await owner.close();
        const fresh = await browser.newContext({ storageState: { cookies, origins: [] }, viewport: { width: 1440, height: 900 } });
        page = await fresh.newPage();
        page.on("pageerror", error => report.page_errors.push(error.message));
        await page.goto(`${origin}/canvas-app/canvas/${config.sourceKey}`);
        await page.locator(`[data-node-id="${child.id}"]`).waitFor();
        await page.waitForFunction(({ id, width, height }) => [...document.querySelectorAll(`[data-node-id="${id}"] img`)].some(image => image.complete && image.naturalWidth === width && image.naturalHeight === height), { id: child.id, width: targetLongEdge, height: outputHeight });
        await page.getByRole("button", { name: "适合屏幕", exact: true }).click();
        await page.screenshot({ path: resolve(output, "fresh-restored.png") });
        assert.equal(report.uploads, 2);
        report.fresh_context_restored = true;
        assert.deepEqual(report.page_errors, []);
        report.step = "complete";
        return;
    }
    if (["navigation", "account_switch", "source_deleted"].includes(config.scenario)) {
        report.step = "真实上传响应前改变操作上下文";
        let release;
        let ready;
        const gate = new Promise(resolve => { release = resolve; });
        const reached = new Promise(resolve => { ready = resolve; });
        const held = [];
        await page.route(`${origin}${resources}`, async route => {
            if (route.request().method() !== "POST") return route.continue();
            const response = await route.fetch();
            const resource = (await read(response)).resource;
            let finished;
            const completion = new Promise(resolve => { finished = resolve; });
            held.push({ resource, completion });
            if (held.length === childCount) ready();
            await gate;
            await route.fulfill({ response });
            finished();
        });
        await runTool(source);
        await reached;
        report.private_resources = held.map(item => item.resource.id);
        if (config.scenario === "navigation") {
            await page.getByRole("button", { name: "切换画布", exact: true }).click();
            await page.getByRole("button", { name: "恢复目标画布", exact: true }).click();
            await page.waitForURL(`${origin}/canvas-app/canvas/${config.otherSourceKey}`);
            await page.waitForFunction(() => document.querySelectorAll("[data-node-id]").length === 0);
        } else if (config.scenario === "account_switch") {
            await read(await owner.request.post(`${origin}/api/v1/auth/login`, { data: { username: config.memberUsername, password: config.password } }));
            const csrf = (await owner.cookies()).find(cookie => cookie.name === "sd_csrf");
            await owner.setExtraHTTPHeaders({ Origin: origin, "X-CSRF-Token": csrf.value });
            await page.evaluate(async () => {
                const { establishCanvasSession } = await import("/canvas-app/src/services/host-session.ts");
                const { applyUserSession } = await import("/canvas-app/src/lib/user-session.ts");
                await applyUserSession(await establishCanvasSession());
            });
        } else {
            await page.keyboard.press("Escape");
            await page.locator(`[data-node-id="${source.id}"]`).click();
            await page.keyboard.press("Delete");
            await graph(owner, document => document.nodes.length === 0);
        }
        release();
        await Promise.all(held.map(item => item.completion));
        await settle();
        const verification = await login(config.username);
        const canonical = (await read(await verification.request.get(`${origin}${path}/my-document`))).source_document;
        assert.equal(canonical.nodes.length, config.scenario === "source_deleted" ? 0 : 1);
        assert.equal(canonical.connections.length, 0);
        const local = await localGraph();
        assert.ok(!local.memory?.nodes.some(item => item.id !== source.id));
        if (config.scenario === "navigation") {
            const other = await page.evaluate(async key => {
                const { useCanvasStore } = await import("/canvas-app/src/stores/canvas/use-canvas-store.ts");
                return useCanvasStore.getState().projects.find(item => item.id === key);
            }, config.otherSourceKey);
            assert.equal(other.nodes.length, 0);
        }
        if (config.scenario === "account_switch") {
            const state = await page.evaluate(async () => {
                const { getActiveUserScope } = await import("/canvas-app/src/lib/user-scope.ts");
                const { useAssetStore } = await import("/canvas-app/src/stores/use-asset-store.ts");
                return { scope: getActiveUserScope(), assets: useAssetStore.getState().assets };
            });
            assert.equal(state.scope, config.memberId);
            assert.deepEqual(state.assets, []);
        }
        for (const item of held) assert.equal((await member.request.get(`${origin}${resources}/${item.resource.id}/file`)).status(), 404);
        assert.equal(report.uploads, childCount + 1);
        assert.deepEqual(report.page_errors, []);
        report.final_nodes = canonical.nodes;
        report.stopped_before_attachment = true;
        report.step = "complete";
        return;
    }
    let failedHash;
    let peerAdvance;
    const failureUrl = config.scenario === "upload_failure" ? `${origin}${resources}` : `${origin}${commitPath}`;
    await page.route(failureUrl, async route => {
        if (route.request().method() !== "POST") return route.continue();
        if (config.scenario === "conflict") {
            peerAdvance ??= (async () => {
                const latest = await read(await peer.request.get(`${origin}${path}/my-document`));
                latest.source_document.nodes[0].title = "另一窗口的新版原图";
                await read(await peer.request.post(`${origin}${commitPath}`, {
                    headers: { "Idempotency-Key": "grid-recovery-owner-peer-advance" },
                    data: { expected_row_version: latest.row_version, source_document: latest.source_document },
                }));
                report.server_document = (await read(await owner.request.get(`${origin}${path}/my-document`))).source_document;
                report.failure_injections.push({ path: commitPath, status: 409, real_owner_peer_commit: true });
            })();
            await peerAdvance;
            return route.continue();
        }
        if (config.scenario === "upload_failure") {
            const hash = pngHash(route.request().postDataBuffer());
            failedHash ??= hash;
            if (hash !== failedHash) return route.continue();
        }
        report.failure_injections.push({ path: new URL(route.request().url()).pathname, status: 503 });
        return route.fulfill({ status: 503, json: { error: { code: "test_grid_unavailable", message: "仅宫格指定上传或提交阶段HTTP503故障注入" } } });
    });
    const failed = page.waitForResponse(response => response.status() === (config.scenario === "conflict" ? 409 : 503) && new URL(response.url()).pathname === (config.scenario === "upload_failure" ? resources : commitPath));
    await runTool(source);
    await failed;
    await page.getByRole("button", { name: config.scenario === "conflict" ? "画布保存状态：版本冲突 · 未同步" : "画布保存状态：云端未保存", exact: true }).waitFor({ state: "visible" });
    const error = page.getByRole("alert").filter({ hasText: config.scenario === "upload_failure" ? "画布媒体尚未持久化" : config.scenario === "conflict" ? "画布已被其他入口修改" : "服务暂时不可用" });
    await error.waitFor({ state: "visible" });
    report.visible_error = await error.innerText();
    const kept = await draft();
    report.source_node_id = source.id;
    report.children = kept.nodes.filter(item => item.id !== source.id).map(item => ({ id: item.id, title: item.title, storageKey: item.metadata.storageKey }));
    assert.equal(report.children.length, childCount);
    assert.equal(new Set(report.children.map(item => item.id)).size, childCount);
    assert.deepEqual((await read(await owner.request.get(`${origin}${path}/my-document`))).source_document.nodes, (report.server_document || original).nodes);
    for (const child of report.children) {
        if (child.storageKey.startsWith("resource:")) assert.equal((await member.request.get(`${origin}${resources}/${child.storageKey.slice(9)}/file`)).status(), 404);
    }
    assert.ok(report.failure_injections.length > 0);
    assert.deepEqual(report.page_errors, [], "宫格网络失败必须由服务边界处理，不能产生未捕获Promise");
    report.failure_kept_private_draft = true;
    await page.screenshot({ path: resolve(output, "failed-draft.png") });

    if (config.scenario === "conflict") {
        report.step = "冲突暂停提交并明确加载最新版本";
        const before = report.commits.length;
        await page.locator("[data-canvas-viewport]").click({ position: { x: 500, y: 650 } });
        await page.keyboard.press("Control+s");
        await settle();
        assert.equal(report.commits.length, before, "409后不得自动覆盖或重新提交");
        await page.getByRole("button", { name: "画布保存状态：版本冲突 · 未同步", exact: true }).click();
        await page.getByRole("button", { name: "加载云端最新版本", exact: true }).click();
        await page.getByRole("button", { name: "编辑节点名称：另一窗口的新版原图", exact: true }).waitFor({ state: "visible" });
        const drafts = await page.evaluate(async key => {
            const { readCanvasSyncDrafts } = await import("/canvas-app/src/services/canvas-sync-drafts.ts");
            return readCanvasSyncDrafts(key);
        }, config.sourceKey);
        assert.ok(drafts.some(item => report.children.every(child => item.project.nodes.some(node => node.id === child.id))));
        report.final_nodes = (await read(await owner.request.get(`${origin}${path}/my-document`))).source_document.nodes;
        assert.deepEqual(report.final_nodes, report.server_document.nodes);
        report.conflict_draft_preserved = true;
        assert.deepEqual(report.page_errors, []);
        report.step = "complete";
        return;
    }

    report.step = "明确保存恢复同组节点";
    await page.unroute(failureUrl);
    const beforeRecovery = report.uploads;
    await page.locator("[data-canvas-viewport]").click({ position: { x: 500, y: 650 } });
    await page.keyboard.press("Control+s");
    const final = await graph(owner, document => document.nodes.length === childCount + 1 && document.connections.length === childCount && report.children.every(child => document.nodes.some(item => item.id === child.id && item.metadata.storageKey.startsWith("resource:"))));
    assert.deepEqual(final.nodes.find(item => item.id === source.id), source);
    assert.equal(report.uploads - beforeRecovery, config.scenario === "upload_failure" ? 1 : 0, "恢复只上传失败的格子，不能重新切图或重传已经完成的格子");
    if (config.scenario === "commit_failure") {
        const attempts = report.commits.slice(startingCommits);
        assert.ok(attempts.length >= 2);
        assert.equal(attempts[0].key, attempts[1].key);
        assert.deepEqual(attempts[0].body, attempts[1].body);
    }
    report.final_nodes = final.nodes;
    report.same_children_recovered = true;
    for (const child of final.nodes.filter(item => item.id !== source.id)) {
        const resourceId = child.metadata.storageKey.slice(9);
        const downloaded = await owner.request.get(`${origin}${resources}/${resourceId}/file`);
        assert.ok(downloaded.ok());
        report.children.find(item => item.id === child.id).sha256 = createHash("sha256").update(await downloaded.body()).digest("hex");
    }
    await page.screenshot({ path: resolve(output, "recovered.png") });
    assert.deepEqual(report.page_errors, []);
    report.step = "complete";
    })();
} catch (error) {
    report.failure_stack = error.stack;
    if (page) await page.screenshot({ path: resolve(output, "failure.png") }).catch(() => {});
    if (page) await writeFile(resolve(output, "failure-dom.txt"), await page.locator("body").ariaSnapshot()).catch(() => {});
    throw error;
} finally {
    await writeFile(resolve(output, "report.json"), JSON.stringify(report, null, 2) + "\n");
    await browser?.close();
    await server.close();
}
process.stdout.write(JSON.stringify(report));
