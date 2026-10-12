import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { chromium } from "playwright";
import { createServer } from "vite";

let input = "";
for await (const chunk of process.stdin) input += chunk;
const config = JSON.parse(input);
const origin = "http://127.0.0.1:4199";
const path = `/api/v1/projects/${config.projectId}/canvases/${config.canvasId}`;
const output = resolve(config.outputDirectory);
await mkdir(output, { recursive: true });
const report = { step: "start", kind: config.kind, width: config.width, page_errors: [], http_errors: [], resource_posts: 0, task_posts: 0 };
const server = await createServer({ cacheDir: resolve(".runtime/vite-media-composition"), server: { host: "127.0.0.1", port: 4199, strictPort: true, proxy: { "/api": { target: config.apiUrl, changeOrigin: true } } }, logLevel: "silent" });
await server.listen();
let browser;
let page;
let owner;
const card = id => page.locator(`[data-node-id="${id}"]`);
const timeline = () => page.getByRole("dialog").filter({ has: page.getByText("多轨时间线", { exact: true }) });
const subtitles = () => page.getByRole("dialog").filter({ has: page.getByText("字幕编辑", { exact: true }) });
const originalEntries = [{ index: 1, startMs: 200, endMs: 800, text: "旧字幕" }];
const importedEntries = [{ index: 1, startMs: 200, endMs: 600, text: "首句中文字幕" }, { index: 2, startMs: 900, endMs: 1400, text: "末句中文字幕" }];
const srt = "1\n00:00:00,200 --> 00:00:00,600\n首句中文字幕\n\n2\n00:00:00,900 --> 00:00:01,400\n末句中文字幕\n";

async function read(response) {
    assert.ok(response.ok(), `HTTP ${response.status()} ${new URL(response.url()).pathname}: ${await response.text()}`);
    return response.json();
}
async function eventually(readValue, predicate, timeout = 60000) {
    const deadline = Date.now() + timeout;
    let value;
    while (Date.now() < deadline) {
        value = await readValue();
        if (predicate(value)) return value;
        await new Promise(resolve => setTimeout(resolve, 100));
    }
    assert.fail(`媒体组合状态未达到预期：${JSON.stringify(value)}`);
}
async function context(username, cookies) {
    const value = await browser.newContext({ viewport: { width: config.width, height: 900 }, ...(cookies ? { storageState: { cookies, origins: [] } } : {}) });
    value.setDefaultTimeout(20000);
    value.setDefaultNavigationTimeout(60000);
    if (!cookies) await read(await value.request.post(`${origin}/api/v1/auth/login`, { headers: { Origin: origin }, data: { username, password: config.password } }));
    const csrf = (await value.cookies()).find(cookie => cookie.name === "sd_csrf");
    assert.ok(csrf);
    await value.setExtraHTTPHeaders({ Origin: origin, "X-CSRF-Token": csrf.value });
    return value;
}
async function graph(context = owner, predicate = () => true) {
    return eventually(async () => (await read(await context.request.get(`${origin}${path}/my-document`))).source_document, predicate);
}
async function fit() {
    const focus = page.getByRole("toolbar", { name: "专注模式工具栏", exact: true });
    if (await focus.isVisible()) await focus.getByRole("button", { name: "退出专注模式", exact: true }).press("Control+0");
    else await page.getByRole("button", { name: "适合屏幕", exact: true }).click();
    await page.waitForFunction(async () => {
        const viewport = document.querySelector("[data-canvas-viewport]");
        let previous;
        let stable = 0;
        while (stable < 8) {
            await new Promise(requestAnimationFrame);
            const next = ["--canvas-live-x", "--canvas-live-y", "--canvas-live-scale"].map(key => viewport.style.getPropertyValue(key)).join(",");
            stable = next === previous ? stable + 1 : 0;
            previous = next;
        }
        return true;
    });
}
async function open(context) {
    page = await context.newPage();
    page.on("pageerror", error => report.page_errors.push(error.stack ?? error.message));
    page.on("request", request => {
        const route = new URL(request.url()).pathname;
        if (request.method() === "POST" && route === "/api/v1/canvas-runtime/resources") report.resource_posts++;
        if (request.method() === "POST" && route === "/api/v1/canvas-runtime/tasks") report.task_posts++;
    });
    page.on("response", response => {
        if (response.status() >= 400) report.http_errors.push({ method: response.request().method(), path: new URL(response.url()).pathname, status: response.status() });
    });
    await page.goto(`${origin}/canvas-app/canvas/${config.sourceKey}`);
    await page.locator("[data-canvas-viewport]").waitFor({ state: "visible" });
    await page.evaluate(() => document.fonts.ready);
    for (const id of config.sourceNodeIds) await card(id).waitFor({ state: "visible" });
    await fit();
}
async function previews(requireAssetIds = false) {
    return graph(owner, document => document.nodes.every(node => (!requireAssetIds || node.metadata?.assetId) && node.metadata.videoPreview?.captureVersion === 2 && node.metadata.videoPreview.sourceKey === node.metadata.storageKey));
}
async function saved() {
    await page.getByRole("button", { name: /^画布保存状态：/ }).press("Control+s");
    await page.getByRole("button", { name: "画布保存状态：已保存", exact: true }).waitFor({ state: "visible" });
}
async function openTimeline() {
    await fit();
    await card("composition-video-1").click({ position: { x: 12, y: 36 } });
    await card("composition-video-1").hover();
    await page.getByRole("button", { name: "进入剪辑", exact: true }).click();
    await timeline().waitFor({ state: "visible" });
}
async function editSubtitles() {
    await openTimeline();
    await timeline().locator('[data-timeline-clip-kind="subtitle"]').first().click();
    await timeline().getByRole("button", { name: "精细编辑（SRT/高亮/样式）", exact: true }).click();
    await subtitles().waitFor({ state: "visible" });
}
async function importSrt(content) {
    const chooser = page.waitForEvent("filechooser");
    await subtitles().getByRole("button", { name: "导入 SRT", exact: true }).click();
    await (await chooser).setFiles({ name: "original.srt", mimeType: "text/plain", buffer: Buffer.from(content) });
}

try {
    browser = await chromium.launch({ headless: true, executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH || undefined });
    owner = await context(config.username);
    await open(owner);
    await previews();
    report.step = "operate";
    let outputNode;
    if (config.kind === "merge") {
        await card("composition-video-1").click({ position: { x: 12, y: 36 } });
        await card("composition-video-2").click({ modifiers: ["Shift"], position: { x: 12, y: 36 } });
        const tools = page.getByRole("toolbar", { name: "多选节点布局工具", exact: true });
        await tools.waitFor({ state: "visible" });
        await writeFile(resolve(output, "original-merge.yml"), await tools.ariaSnapshot());
        await tools.getByRole("button", { name: "合并选中视频（2）", exact: true }).click();
        await graph(owner, document => document.nodes.length === 3 && document.nodes.every(node => node.metadata?.assetId));
        const complete = await previews(true);
        outputNode = complete.nodes.find(node => !config.sourceNodeIds.includes(node.id));
        assert.ok(outputNode && outputNode.metadata.status === "success");
        for (const id of config.sourceNodeIds) assert.ok(complete.connections.some(edge => edge.fromNodeId === id && edge.toNodeId === outputNode.id));
        const response = await owner.request.get(`${origin}/api/v1/canvas-runtime/resources/${outputNode.metadata.storageKey.slice("resource:".length)}/file`);
        assert.ok(response.ok());
        const bytes = await response.body();
        await writeFile(resolve(output, "merged.mp4"), bytes);
        report.output_node_id = outputNode.id;
        report.output_resource_id = outputNode.metadata.storageKey.slice("resource:".length);
        report.output_checksum = createHash("sha256").update(bytes).digest("hex");
        await fit();
    } else {
        assert.equal(config.kind, "subtitles");
        await editSubtitles();
        await writeFile(resolve(output, "original-subtitles.yml"), await subtitles().ariaSnapshot());
        await importSrt("这不是有效 SRT");
        await page.getByText(/未解析到有效字幕：文件需为 SRT 格式/).first().waitFor({ state: "attached" });
        assert.equal(await subtitles().getByPlaceholder("字幕文本", { exact: true }).first().inputValue(), "旧字幕");
        await importSrt(srt);
        await eventually(() => subtitles().getByPlaceholder("字幕文本", { exact: true }).count(), count => count === 2);
        assert.equal(await subtitles().getByPlaceholder("字幕文本", { exact: true }).nth(1).inputValue(), "末句中文字幕");
        const downloading = page.waitForEvent("download");
        await subtitles().getByRole("button", { name: "导出 SRT", exact: true }).click();
        const download = await downloading;
        assert.equal(download.suggestedFilename(), "组合来源1.srt");
        await download.saveAs(resolve(output, "exported.srt"));
        assert.equal(await readFile(resolve(output, "exported.srt"), "utf8"), srt);
        report.srt_round_trip = true;
        await subtitles().getByRole("button", { name: /^取\s*消$/ }).click();
        await subtitles().waitFor({ state: "hidden" });
        assert.deepEqual((await graph()).nodes[0].metadata.subtitleEntries, originalEntries);
        report.cancel_preserved = true;
        await editSubtitles();
        await importSrt(srt);
        await subtitles().getByPlaceholder("字幕文本", { exact: true }).nth(1).waitFor({ state: "visible" });
        await page.screenshot({ path: resolve(output, "subtitles-imported.png"), fullPage: true });
        await subtitles().getByRole("button", { name: /^保\s*存$/ }).click();
        await subtitles().waitFor({ state: "hidden" });
        await graph(owner, document => document.nodes[0].metadata.subtitleEntries?.length === 2);
        await openTimeline();
        assert.equal(await timeline().locator('[data-timeline-clip-kind="subtitle"]').count(), 2);
        await timeline().getByRole("button", { name: /^保\s*存$/ }).click();
        await timeline().waitFor({ state: "hidden" });
        const synced = await graph(owner, document => document.timeline?.clips.filter(clip => clip.kind === "subtitle").length === 2);
        assert.deepEqual(synced.nodes[0].metadata.subtitleEntries, importedEntries);
        report.saved_entries = synced.nodes[0].metadata.subtitleEntries;
    }
    await saved();
    const beforeRestore = await graph();
    const uploads = report.resource_posts;
    const cookies = await owner.cookies();
    await owner.close();
    owner = await context(undefined, cookies);
    report.step = "fresh";
    await open(owner);
    const restored = await graph();
    for (const node of beforeRestore.nodes) assert.deepEqual(restored.nodes.find(item => item.id === node.id).metadata, node.metadata);
    if (outputNode) {
        await card(outputNode.id).getByRole("button", { name: /^播放 / }).first().click();
        await eventually(() => card(outputNode.id).locator("video").first().evaluate(element => element.videoWidth === 320 && element.readyState >= 2), Boolean);
    } else {
        await editSubtitles();
        assert.equal(await subtitles().getByPlaceholder("字幕文本", { exact: true }).nth(1).inputValue(), "末句中文字幕");
        await subtitles().getByRole("button", { name: "清空全部", exact: true }).click();
        const confirmation = page.getByRole("dialog").filter({ has: page.getByText("清空全部字幕", { exact: true }) });
        await confirmation.getByRole("button", { name: /^取\s*消$/ }).click();
        assert.equal(await subtitles().getByPlaceholder("字幕文本", { exact: true }).count(), 2);
        await subtitles().getByRole("button", { name: "清空全部", exact: true }).click();
        await confirmation.getByRole("button", { name: /^清\s*空$/ }).click();
        await graph(owner, document => document.nodes[0].metadata.subtitleEntries?.length === 0 && document.timeline.clips.every(clip => clip.kind !== "subtitle"));
        await subtitles().getByRole("button", { name: /^取\s*消$/ }).click();
        await subtitles().waitFor({ state: "hidden" });
        await saved();
        await openTimeline();
        assert.equal(await timeline().locator('[data-timeline-clip-kind="subtitle"]').count(), 0);
        await timeline().getByRole("button", { name: /^取\s*消$/ }).click();
        report.clear_persisted_without_save = true;
    }
    const final = await graph();
    const member = await context(config.memberUsername);
    const shared = await graph(member);
    for (const node of final.nodes) {
        const memberNode = shared.nodes.find(item => item.id === node.id);
        assert.equal(memberNode.metadata.storageKey, node.metadata.storageKey);
        const response = await member.request.get(`${origin}/api/v1/canvas-runtime/resources/${node.metadata.storageKey.slice("resource:".length)}/file`);
        assert.ok(response.ok());
        if (node.id === outputNode?.id) assert.equal(createHash("sha256").update(await response.body()).digest("hex"), report.output_checksum);
        if (!outputNode) assert.deepEqual(memberNode.metadata.subtitleEntries, []);
    }
    report.cookie_only_recovery = true;
    assert.equal(report.resource_posts, uploads, "Cookies 恢复不能重复上传媒体或预览");
    assert.equal(report.task_posts, 0);
    assert.deepEqual(report.page_errors, []);
    assert.deepEqual(report.http_errors, []);
    await page.screenshot({ path: resolve(output, "media-composition-restored.png"), fullPage: true });
    report.step = "complete";
} catch (error) {
    report.failure = error.stack ?? String(error);
    if (page && !page.isClosed()) await page.screenshot({ path: resolve(output, "failure.png"), fullPage: true }).catch(() => undefined);
    process.exitCode = 1;
} finally {
    await browser?.close();
    await new Promise(resolve => server.httpServer.close(resolve));
    await server.close();
    await writeFile(resolve(output, "report.json"), JSON.stringify(report, null, 2));
    process.stdout.write(JSON.stringify(report));
}
