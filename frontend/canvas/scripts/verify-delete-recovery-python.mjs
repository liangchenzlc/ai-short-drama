import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";
import { resolve } from "node:path";
import { chromium } from "playwright";
import { createServer } from "vite";

let input = "";
for await (const chunk of process.stdin) input += chunk;
const config = JSON.parse(input);
const origin = "http://127.0.0.1:4189";
const output = resolve(".runtime/delete-recovery-python-browser");
await mkdir(output, { recursive: true });
const server = await createServer({ server: { host: "127.0.0.1", port: 4189, strictPort: true, proxy: { "/api": { target: config.apiUrl, changeOrigin: true } } }, logLevel: "silent" });
await server.listen();
let browser;
let page;
const report = { step: "start", page_errors: [], delete_requests: [], status_reads: 0 };
async function read(response) {
    assert.ok(response.ok(), `HTTP ${response.status()}: ${await response.text()}`);
    return response.json();
}
try {
    browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH });
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    context.setDefaultTimeout(20000);
    await read(await context.request.post(`${origin}/api/v1/auth/login`, { headers: { Origin: origin }, data: { username: config.username, password: config.password } }));
    const csrf = (await context.cookies()).find(cookie => cookie.name === "sd_csrf");
    await context.setExtraHTTPHeaders({ Origin: origin, "X-CSRF-Token": csrf.value });
    const project = await read(await context.request.post(`${origin}/api/v1/projects`, {
        headers: { "Idempotency-Key": "delete-recovery-project" }, data: { name: "未知删除回执作品", aspect: "16:9", workspace_mode: "infinite_canvas" },
    }));
    const path = `/api/v1/projects/${project.id}/canvases/${project.primary_canvas_id}`;
    const initial = await read(await context.request.get(origin + path + "/my-document"));
    const document = { ...initial.source_document, title: "未知删除回执作品", nodes: [{ id: "note", type: "text", title: "原稿", position: { x: 0, y: 0 }, width: 320, height: 220, metadata: { content: "刷新后不能丢失的作品", prompt: "本人私人参数" } }] };
    await read(await context.request.post(origin + path + "/commits", { headers: { "Idempotency-Key": "delete-recovery-content" }, data: { expected_row_version: document.revision, source_document: document } }));
    const before = (await read(await context.request.get(origin + path + "/my-document"))).source_document;
    page = await context.newPage();
    page.on("pageerror", error => report.page_errors.push(error.message));
    page.on("request", request => { if (new URL(request.url()).pathname.endsWith("/recycle-status")) report.status_reads += 1; });
    await page.goto(`${origin}/canvas-app/canvas/${initial.source_key}`);
    await page.locator('[data-node-id="note"]').waitFor({ timeout: 60000 });
    await page.goto(`${origin}/canvas-app/canvas`);
    await page.getByRole("button", { name: "未知删除回执作品 画布操作", exact: true }).waitFor();
    let loseAck = true;
    let failBeforeDispatch = false;
    await page.route(`**${path}`, async route => {
        if (route.request().method() !== "DELETE") return route.continue();
        report.delete_requests.push({ key: route.request().headers()["idempotency-key"], body: route.request().postDataJSON() });
        if (failBeforeDispatch) {
            failBeforeDispatch = false;
            return route.fulfill({ status: 503, json: { error: { code: "unavailable", message: "删除未发送测试" } } });
        }
        const response = await route.fetch();
        assert.equal(response.status(), 200, await response.text());
        if (loseAck) {
            loseAck = false;
            await route.fulfill({ status: 503, json: { error: { code: "unavailable", message: "删除回执测试中断" } } });
        } else await route.fulfill({ response });
    });
    report.step = "archive succeeds but response is lost";
    await page.getByRole("button", { name: "未知删除回执作品 画布操作", exact: true }).click();
    await page.getByRole("menuitem", { name: "删除项目", exact: true }).click();
    await page.getByText("删除画布失败：服务暂时不可用，请稍后重试。", { exact: true }).waitFor({ state: "attached" });
    assert.equal((await context.request.get(origin + path)).status(), 404);
    await page.reload();
    await page.getByRole("button", { name: "回收站", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "回收站", exact: true });
    await dialog.getByRole("heading", { name: "未知删除回执作品", exact: true }).waitFor();
    assert.equal(report.delete_requests.length, 1, "刷新只能查询回执，不能重新发送 DELETE");
    assert.ok(report.status_reads > 0);
    report.lost_delete_ack_recovers_snapshot_without_write_replay = true;
    await page.reload();
    await page.getByRole("button", { name: "回收站", exact: true }).click();
    await dialog.getByRole("heading", { name: "未知删除回执作品", exact: true }).waitFor();
    assert.equal(await dialog.locator(".recycle-bin-card").count(), 1);
    report.recovered_card_survives_second_reload = true;
    await dialog.getByRole("checkbox", { name: "全选回收站项目", exact: true }).check();
    await dialog.getByRole("button", { name: "恢复到项目列表", exact: true }).click();
    await dialog.getByText("回收站是空的", { exact: true }).waitFor();
    const restored = (await read(await context.request.get(origin + path + "/my-document"))).source_document;
    assert.deepEqual(restored.nodes, before.nodes);
    assert.equal(restored.id, before.id);
    assert.equal(restored.workspaceProjectId, before.workspaceProjectId);
    assert.equal(BigInt(restored.revision), BigInt(before.revision) + 2n);
    report.restored_same_work_and_private_parameters = true;
    report.step = "another client restores while the original archive acknowledgment is unknown";
    await page.keyboard.press("Escape");
    await page.reload();
    loseAck = true;
    await page.getByRole("button", { name: "未知删除回执作品 画布操作", exact: true }).click();
    await page.getByRole("menuitem", { name: "删除项目", exact: true }).click();
    await page.getByText("删除画布失败：服务暂时不可用，请稍后重试。", { exact: true }).waitFor({ state: "attached" });
    assert.equal(report.delete_requests.length, 2);
    const currentArchiveKey = report.delete_requests.at(-1).key;
    await read(await context.request.post(`${origin}/api/v1/canvas-runtime/canvas-projects/${before.id}/recycle-restore`, {
        headers: { "Idempotency-Key": "other-client-restores-unknown-archive" }, data: { archive_key: currentArchiveKey },
    }));
    await page.reload();
    await page.getByRole("button", { name: "未知删除回执作品 画布操作", exact: true }).waitFor();
    await page.getByRole("button", { name: "回收站", exact: true }).click();
    await dialog.getByText("回收站是空的", { exact: true }).waitFor();
    const newer = (await read(await context.request.get(origin + path + "/my-document"))).source_document;
    assert.equal(BigInt(newer.revision), BigInt(restored.revision) + 2n);
    assert.deepEqual(newer.nodes, before.nodes);
    assert.equal(report.delete_requests.length, 2);
    report.superseded_archive_keeps_current_work = true;
    report.step = "uncommitted delete waits for explicit retry with the same key and body";
    await page.keyboard.press("Escape");
    failBeforeDispatch = true;
    await page.getByRole("button", { name: "未知删除回执作品 画布操作", exact: true }).click();
    await page.getByRole("menuitem", { name: "删除项目", exact: true }).click();
    await page.getByText("删除画布失败：服务暂时不可用，请稍后重试。", { exact: true }).waitFor({ state: "attached" });
    assert.equal(report.delete_requests.length, 3);
    const beforeReloadReads = report.status_reads;
    const checked = page.waitForResponse(response => new URL(response.url()).pathname.endsWith("/recycle-status"));
    await page.reload();
    assert.equal((await checked).status(), 404);
    await page.getByRole("button", { name: "回收站", exact: true }).click();
    await dialog.getByText("回收站是空的", { exact: true }).waitFor();
    assert.equal((await read(await context.request.get(origin + path))).row_version, newer.revision);
    assert.equal(report.delete_requests.length, 3);
    assert.ok(report.status_reads > beforeReloadReads);
    await page.keyboard.press("Escape");
    await page.getByRole("button", { name: "未知删除回执作品 画布操作", exact: true }).click();
    await page.getByRole("menuitem", { name: "删除项目", exact: true }).click();
    await page.getByRole("button", { name: "未知删除回执作品 画布操作", exact: true }).waitFor({ state: "hidden" });
    await page.getByRole("button", { name: "回收站", exact: true }).click();
    await dialog.getByRole("heading", { name: "未知删除回执作品", exact: true }).waitFor();
    assert.equal(report.delete_requests.length, 4);
    assert.deepEqual(report.delete_requests[2], report.delete_requests[3]);
    assert.equal((await context.request.get(origin + path)).status(), 404);
    report.uncommitted_delete_requires_explicit_same_request_retry = true;
    assert.deepEqual(report.page_errors, []);
    report.step = "complete";
    process.stdout.write(JSON.stringify(report));
} catch (error) {
    if (page && !page.isClosed()) await page.screenshot({ path: resolve(output, "failure.png") }).catch(() => undefined);
    process.stderr.write(JSON.stringify(report) + "\n" + (error.stack || String(error)));
    process.exitCode = 1;
} finally {
    await browser?.close();
    await server.close();
}
