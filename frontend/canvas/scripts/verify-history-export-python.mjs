import assert from "node:assert/strict";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { unzipSync } from "fflate";
import { chromium } from "playwright";
import { createServer } from "vite";

let input = "";
for await (const chunk of process.stdin) input += chunk;
const config = JSON.parse(input);
const origin = "http://127.0.0.1:4189";
const output = resolve(config.outputDirectory);
await mkdir(output, { recursive: true });
const report = {
    actor: config.actor, kind: config.kind, width: config.width, step: "start",
    page_errors: [], unexpected_failures: [], writes: [], downloads: [], media_reads: [],
};
const server = await createServer({
    server: { host: "127.0.0.1", port: 4189, strictPort: true,
        proxy: { "/api": { target: config.apiUrl, changeOrigin: true } } },
    logLevel: "silent",
});
await server.listen();
let browser;
let page;
let releaseHistory;
const injected = new Set();

async function read(response) {
    assert.ok(response.ok(), `HTTP ${response.status()} ${new URL(response.url()).pathname}: ${await response.text()}`);
    return response.json();
}

async function authenticate(context, username) {
    await read(await context.request.post(origin + "/api/v1/auth/login", {
        headers: { Origin: origin }, data: { username, password: config.password },
    }));
    const csrf = (await context.cookies()).find(cookie => cookie.name === "sd_csrf");
    assert.ok(csrf);
    await context.setExtraHTTPHeaders({ Origin: origin, "X-CSRF-Token": csrf.value });
}

async function newContext(cookies) {
    const context = await browser.newContext({ viewport: { width: config.width, height: 900 }, acceptDownloads: true });
    context.setDefaultTimeout(20000);
    if (cookies) {
        await context.addCookies(cookies);
        const csrf = cookies.find(cookie => cookie.name === "sd_csrf");
        await context.setExtraHTTPHeaders({ Origin: origin, "X-CSRF-Token": csrf.value });
    } else await authenticate(context, config.username);
    return context;
}

function observe(target) {
    target.on("pageerror", error => report.page_errors.push(error.message));
    target.on("download", download => report.downloads.push(download.suggestedFilename()));
    target.on("request", request => {
        const path = new URL(request.url()).pathname;
        if (path.startsWith("/api/v1/") && request.method() !== "GET"
            && !["/my-state", "/viewport", "/view-preferences", "/resources/normalize", "/auth/logout", "/auth/login"].some(suffix => path.endsWith(suffix))) {
            report.writes.push({ method: request.method(), path, ...(path.endsWith("/commits") ? { body: request.postDataJSON() } : {}) });
        }
    });
    target.on("response", response => {
        const url = new URL(response.url());
        if (url.pathname.includes("/resources/") && url.pathname.endsWith("/file") && url.searchParams.get("proxy") === "1") {
            report.media_reads.push({ id: url.pathname.split("/").at(-2), status: response.status() });
        }
        if (url.pathname.startsWith("/api/v1/") && response.status() >= 400
            && !injected.has(response.url())) report.unexpected_failures.push({ path: url.pathname, status: response.status() });
    });
}

async function openCanvas(target = page) {
    await target.goto(`${origin}/canvas-app/canvas/${config.sourceKey}`);
    await target.locator("[data-canvas-editor] [data-canvas-viewport]").waitFor({ timeout: 60000 });
    const close = target.getByRole("button", { name: "关闭助手", exact: true });
    if (await close.isVisible()) await close.click();
}

async function draftState(target = page) {
    return target.evaluate(async ({ key, owner, member }) => {
        const { readCanvasSyncDrafts } = await import("/canvas-app/src/services/canvas-sync-drafts.ts");
        const { getActiveUserScope } = await import("/canvas-app/src/lib/user-scope.ts");
        return { scope: getActiveUserScope(), own: await readCanvasSyncDrafts(key, owner), other: await readCanvasSyncDrafts(key, member) };
    }, { key: config.sourceKey, owner: config.ownerId, member: config.memberId });
}

async function visiblePanel() {
    const panel = page.locator(".canvas-version-panel:visible");
    await panel.waitFor();
    return panel;
}

async function selectPreview({ delay = false } = {}) {
    await page.getByRole("button", { name: "版本记录", exact: true }).click();
    let panel = await visiblePanel();
    if (config.kind === "draft") {
        await panel.getByRole("tab", { name: /^已保存版本/ }).focus();
        await page.keyboard.press("End");
        assert.equal(await panel.getByRole("tab", { name: /^本机草稿/ }).getAttribute("aria-selected"), "true");
        await page.keyboard.press("Home");
        assert.equal(await panel.getByRole("tab", { name: /^已保存版本/ }).getAttribute("aria-selected"), "true");
        await page.keyboard.press("ArrowRight");
        await panel.locator(".canvas-version-draft").filter({ hasText: `基于 v${config.historyRevision}` }).click();
    } else {
        if (delay) {
            const held = new Promise(resolve => { releaseHistory = resolve; });
            await page.route(origin + config.path + `/revisions/${config.historyId}`, async route => {
                await held;
                await route.continue();
            });
        }
        await panel.locator(".canvas-version-item").filter({ has: page.getByText(`v${config.historyRevision}`, { exact: true }) }).click();
    }
    await page.locator("[data-canvas-version-preview]").waitFor();
    if (config.width < 1024) {
        await page.locator(".canvas-version-panel").waitFor({ state: "hidden" });
        await page.locator("[data-canvas-version-preview]").getByRole("button", { name: "版本", exact: true }).click();
        panel = await visiblePanel();
    }
    const download = panel.getByRole("button", { name: config.kind === "draft" ? "下载草稿" : "下载此版本", exact: true });
    if (delay) {
        assert.equal(await download.isDisabled(), true, "版本读取中不能导出空项目");
        releaseHistory();
        releaseHistory = undefined;
    }
    await download.waitFor();
    await page.waitForFunction(label => [...document.querySelectorAll(".canvas-version-panel button")].some(button => button.textContent.trim() === label && !button.disabled), config.kind === "draft" ? "下载草稿" : "下载此版本");
    await page.waitForFunction(width => [...document.querySelectorAll(".canvas-version-panel")].some(element => {
        const bounds = element.getBoundingClientRect();
        return bounds.width > 0 && bounds.x >= -1 && bounds.right <= width + 1;
    }), config.width);
    const box = await panel.boundingBox();
    assert.ok(box && box.x >= -1 && box.x + box.width <= config.width + 1, "版本面板应在屏幕内");
    const action = await download.boundingBox();
    assert.ok(action && action.y >= 0 && action.y + action.height <= 900, "下载操作应可见");
    return download;
}

async function currentDocument(context) {
    return (await read(await context.request.get(origin + config.path + "/my-document"))).source_document;
}

function assertGraphUnchanged(before, after) {
    for (const key of ["nodes", "connections", "timeline", "revision"]) assert.deepEqual(after[key], before[key], key);
}

try {
    browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH });
    const context = await newContext();
    page = await context.newPage();
    observe(page);
    const before = await currentDocument(context);
    assert.equal(before.revision, config.currentRevision);
    await openCanvas();
    report.step = "prepare a separate private draft without replacing the current graph";
    if (config.kind === "draft") {
        await page.evaluate(async project => {
            const { preserveCanvasSyncDraft } = await import("/canvas-app/src/services/canvas-sync-drafts.ts");
            await preserveCanvasSyncDraft(project);
        }, config.draftDocument);
        const stored = await draftState();
        assert.equal(stored.scope, config.userId);
        assert.equal(stored.own.length, 1);
        assert.deepEqual(stored.own[0].project, config.draftDocument);
        assert.deepEqual(stored.other, []);
        await page.reload();
        await page.locator("[data-canvas-editor] [data-canvas-viewport]").waitFor({ timeout: 60000 });
        assert.deepEqual((await draftState()).own, stored.own, "独立草稿应在刷新后保持原内容");
        report.draft_preparation = "preserveCanvasSyncDraft function, followed by original UI download";
    }
    report.step = "select an old version or private draft through the original version panel";
    const download = await selectPreview({ delay: config.kind === "cloud" });
    await page.screenshot({ path: resolve(output, "preview.png") });
    report.step = "reject an incomplete archive and report every missing timeline file";
    let fail = true;
    for (const id of config.failureIds) {
        await page.route(url => url.pathname === `/api/v1/canvas-runtime/resources/${id}/file` && url.searchParams.get("proxy") === "1", route => {
            if (!fail) return route.continue();
            injected.add(route.request().url());
            return route.fulfill({ status: 503, json: { error: { code: "unavailable", message: "测试注入：旧版媒体读取失败" } } });
        });
    }
    assert.ok(config.failureIds.every(id => !report.media_reads.some(item => item.id === id)), "失败媒体必须在首次导出前尚未缓存");
    await download.click();
    const message = page.locator(".ant-message-notice-content").filter({ hasText: "导出未完成：缺少 2 个文件" });
    await message.waitFor({ state: "attached" });
    const detail = await message.textContent();
    report.export_error = detail;
    report.error_feedback_visible = await message.isVisible();
    assert.equal(report.error_feedback_visible, false, "固定源CSS禁用Toast，保持源状态并单列可见反馈缺口");
    for (const id of config.failureIds) assert.ok(detail.includes(`resource:${id}`), "应列出每项失败媒体");
    assert.equal(report.downloads.length, 0, "失败导出不能下载不完整ZIP");
    report.incomplete_downloads = report.downloads.length;
    assert.ok(config.failureIds.every(id => report.media_reads.some(item => item.id === id && item.status === 503)));
    await page.screenshot({ path: resolve(output, "missing-files.png") });
    report.step = "explicitly retry the same selected snapshot and compare every original byte";
    fail = false;
    const downloaded = page.waitForEvent("download");
    await download.click();
    const archive = await downloaded;
    assert.equal(await archive.failure(), null);
    const zipPath = resolve(output, "snapshot.zip");
    await archive.saveAs(zipPath);
    const zip = unzipSync(await readFile(zipPath));
    assert.ok(zip["projects.json"]);
    const data = JSON.parse(Buffer.from(zip["projects.json"]).toString("utf8"));
    assert.equal(data.app, "infinite-canvas");
    assert.equal(data.version, 4);
    assert.equal(data.projects.length, 1);
    const item = data.projects[0];
    const expected = structuredClone(config.expectedDocument);
    delete expected.remoteContentHash;
    assert.deepEqual(item.project, expected, "ZIP必须冻结所选历史/草稿，不能混当前项目");
    assert.deepEqual(item.drawingDocuments, [], "历史下载不混用当前本机笔画");
    assert.deepEqual(item.files.map(file => file.storageKey).sort(), config.media.map(media => "resource:" + media.id).sort());
    for (const media of config.media) {
        const file = item.files.find(file => file.storageKey === "resource:" + media.id);
        const original = Buffer.from(media.bytes, "base64");
        assert.ok(file.path.includes(encodeURIComponent(expected.id)));
        assert.equal(file.mimeType, media.mime);
        assert.equal(file.bytes, original.length);
        assert.deepEqual(Buffer.from(zip[file.path]), original, file.storageKey);
    }
    assert.equal(Object.keys(zip).length, config.media.length + 1, "不能附带当前图媒体或私人缓存");
    const projectText = JSON.stringify(item.project);
    assert.ok(!projectText.includes("resource:" + config.currentResourceId));
    assert.ok(!projectText.includes("blob:") && !projectText.includes("X-Amz-") && !projectText.includes("minio://"));
    if (config.actor === "member") assert.ok(!projectText.includes("OWNER_"), "成员导出不能包含主人的私人参数");
    assert.ok(config.failureIds.every(id => report.media_reads.filter(item => item.id === id && item.status === 503).length === 1));
    assert.ok(config.failureIds.every(id => report.media_reads.filter(item => item.id === id && item.status === 200).length === 1));
    report.frozen_media_bytes = true;
    report.explicit_retry = true;
    report.step = "return to the unchanged current graph and verify cookies-only recovery";
    if (config.width < 1024) {
        await (await visiblePanel()).getByRole("button", { name: "关闭版本记录", exact: true }).click();
        await page.locator(".canvas-version-panel").waitFor({ state: "hidden" });
        await page.locator("[data-canvas-version-preview]").waitFor({ state: "hidden" });
    } else {
        await page.getByRole("button", { name: "返回当前画布", exact: true }).click();
    }
    assertGraphUnchanged(before, await currentDocument(context));
    report.current_graph_unchanged = true;
    if (config.kind === "draft") {
        const previous = (await draftState()).own.find(draft => draft.project.title === config.draftDocument.title);
        assert.deepEqual(previous.project, config.draftDocument);
        await page.goto("about:blank");
        await read(await context.request.post(origin + "/api/v1/auth/logout"));
        await authenticate(context, config.memberUsername);
        await openCanvas();
        const switched = await draftState();
        assert.equal(switched.scope, config.memberId);
        assert.deepEqual(switched.other, []);
        assert.deepEqual(switched.own.find(draft => draft.id === previous.id), previous, "账号切换不能清除原作者草稿");
        await page.getByRole("button", { name: "版本记录", exact: true }).click();
        await (await visiblePanel()).getByRole("tab", { name: /^本机草稿/ }).click();
        const fallback = page.locator(".canvas-version-draft:visible");
        await fallback.filter({ hasText: `基于 v${config.currentRevision}` }).waitFor();
        assert.equal(await fallback.count(), 1, "源本地模式仅显示当前项目的默认草稿，不显示另一作者的独立草稿");
        assert.ok(!(await fallback.textContent()).includes(`基于 v${config.historyRevision}`));
        report.account_switch_draft_fallback = "current local snapshot only; previous author's draft is absent";
        await page.screenshot({ path: resolve(output, "member-draft-fallback.png") });
    } else {
        const cookies = await context.cookies();
        await context.close();
        const fresh = await newContext(cookies);
        page = await fresh.newPage();
        observe(page);
        await openCanvas();
        assert.equal((await draftState()).scope, config.userId);
        await selectPreview();
        await page.screenshot({ path: resolve(output, "fresh-history.png") });
        assertGraphUnchanged(before, await currentDocument(fresh));
        assert.deepEqual((await read(await fresh.request.get(origin + config.path + `/revisions/${config.historyId}`))).source_document, config.expectedDocument);
    }
    report.download_count = report.downloads.length;
    assert.equal(report.download_count, 1, "重新打开不应自动下载或恢复历史");
    assert.deepEqual(report.writes, []);
    assert.deepEqual(report.page_errors, []);
    assert.deepEqual(report.unexpected_failures, []);
    report.history_read_only = true;
    report.scope_isolation = true;
    report.step = "complete";
    await writeFile(resolve(output, "report.json"), JSON.stringify(report, null, 2));
    process.stdout.write(JSON.stringify(report));
} catch (error) {
    report.failure = { type: error.name, message: error.message, stack: error.stack };
    if (page && !page.isClosed()) await page.screenshot({ path: resolve(output, "failure.png") }).catch(() => undefined);
    await writeFile(resolve(output, "report.json"), JSON.stringify(report, null, 2));
    process.stderr.write(JSON.stringify(report) + "\n" + (error.stack || String(error)));
    process.exitCode = 1;
} finally {
    releaseHistory?.();
    await browser?.close();
    await server.close();
}
