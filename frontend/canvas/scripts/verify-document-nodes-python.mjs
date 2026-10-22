import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { chromium } from "playwright";
import { createServer } from "vite";

let input = "";
for await (const chunk of process.stdin) input += chunk;
const config = JSON.parse(input);
const origin = "http://127.0.0.1:4199";
const path = `/api/v1/projects/${config.projectId}/canvases/${config.canvasId}`;
const output = resolve(config.outputDirectory);
const report = { step: "start", kind: config.kind, width: config.width, page_errors: [], http_errors: [], task_posts: 0, resource_posts: 0 };
await mkdir(output, { recursive: true });
const server = await createServer({ cacheDir: resolve(".runtime/vite-document-nodes-browser"), server: { host: "127.0.0.1", port: 4199, strictPort: true, proxy: { "/api": { target: config.apiUrl, changeOrigin: true } } }, logLevel: "silent" });
await server.listen();
let browser;
let page;
let owner;
let inputId;

const empty = {
    markdown: "连接文本节点，或双击编辑 Markdown",
    svg: "连接输出 SVG 的文本节点",
    html: "连接输出 HTML 的文本节点",
    chart: "连接输出 JSON 数组或 CSV 的文本节点",
    compare: "连接两张图片进行对比",
};
const htmlSource = '<!doctype html><html><body><main id="node-html">页面预览</main><script>document.body.dataset.executed="yes";try{parent.document.body.dataset.documentNodeEscape="yes"}catch{document.body.dataset.parentBlocked="yes"}try{localStorage.getItem("document-node-check")}catch{document.body.dataset.storageBlocked="yes"}</script></body></html>';
const valid = {
    markdown: '# 车站分镜\n\n**第一镜**：角色走入车站。\n\n| 镜号 | 时长 |\n| --- | --- |\n| 1 | 3秒 |\n\n<script>window.documentNodeEscape="yes"</script>\n\n[安全链接](https://example.com/)\n\n[禁止链接](javascript:alert(1))',
    svg: '说明文字\n<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 60"><rect id="node-svg" x="5" y="5" width="90" height="50" fill="#ef4444"/><script>document.documentElement.dataset.executed="yes"</script></svg>\n后续说明',
    html: htmlSource,
    chart: "月份,镜头数,完成数\n一月,12,8\n二月,18,14",
};
const own = {
    markdown: "# 自有文档\n\n自身内容优先于上游正文。",
    svg: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 60"><circle id="own-svg" cx="50" cy="30" r="25" fill="#2563eb"/></svg>',
    html: '<!doctype html><html><body><h1>模板页面</h1><pre id="template-input">{{input}}</pre><script>document.body.dataset.executed="yes";try{parent.document.body.dataset.documentNodeEscape="yes"}catch{document.body.dataset.parentBlocked="yes"}try{localStorage.getItem("document-node-check")}catch{document.body.dataset.storageBlocked="yes"}</script></body></html>',
    chart: '[{"月份":"三月","镜头数":24,"完成数":20},{"月份":"四月","镜头数":30,"完成数":26}]',
};

async function read(response) {
    assert.ok(response.ok(), `HTTP ${response.status()} ${new URL(response.url()).pathname}: ${await response.text()}`);
    return response.json();
}
async function eventually(readValue, predicate) {
    let value;
    for (let attempt = 0; attempt < 150; attempt++) {
        value = await readValue();
        if (predicate(value)) return value;
        await new Promise(resolve => setTimeout(resolve, 100));
    }
    assert.fail(`文档节点状态未达到预期：${JSON.stringify(value)}`);
}
async function graph(context = owner, predicate = () => true) {
    return eventually(async () => (await read(await context.request.get(`${origin}${path}/my-document`))).source_document, predicate);
}
async function context(username, cookies) {
    const result = await browser.newContext({ viewport: { width: config.width, height: 900 }, reducedMotion: "no-preference", ...(cookies ? { storageState: { cookies, origins: [] } } : {}) });
    result.setDefaultTimeout(20000);
    result.setDefaultNavigationTimeout(60000);
    if (!cookies) await read(await result.request.post(`${origin}/api/v1/auth/login`, { headers: { Origin: origin }, data: { username, password: config.password } }));
    const csrf = (await result.cookies()).find(cookie => cookie.name === "sd_csrf");
    assert.ok(csrf);
    await result.setExtraHTTPHeaders({ Origin: origin, "X-CSRF-Token": csrf.value });
    return result;
}
async function open(context) {
    page = await context.newPage();
    page.on("pageerror", error => report.page_errors.push(error.stack ?? error.message));
    page.on("request", request => {
        const route = new URL(request.url()).pathname;
        if (request.method() === "POST" && route === "/api/v1/canvas-runtime/tasks") report.task_posts++;
        if (request.method() === "POST" && route === "/api/v1/canvas-runtime/resources") report.resource_posts++;
    });
    page.on("response", response => {
        const route = new URL(response.url()).pathname;
        if (response.status() >= 400 && route.startsWith("/api/")) report.http_errors.push({ path: route, status: response.status() });
    });
    await page.goto(`${origin}/canvas-app/canvas/${config.sourceKey}`);
    await page.locator("[data-canvas-viewport]").waitFor({ state: "visible" });
    await page.evaluate(() => document.fonts.ready);
}
const viewer = () => page.locator('[data-node-id="existing-document"]');
async function stableViewport() {
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
async function fit() {
    const focus = page.getByRole("toolbar", { name: "专注模式工具栏", exact: true });
    if (await focus.isVisible()) await focus.getByTitle("适应画布", { exact: true }).click();
    else await page.getByRole("button", { name: "适合屏幕", exact: true }).click();
    await stableViewport();
}
async function save() {
    await page.getByRole("button", { name: /^画布保存状态：/ }).press("Control+s");
    await page.locator(".ant-message").getByText(/^画布已保存(?:到云端)?$/, { exact: true }).waitFor({ state: "attached" });
    await page.getByRole("button", { name: "画布保存状态：已保存", exact: true }).waitFor({ state: "visible" });
}
async function editText(content) {
    const text = page.locator(`[data-node-id="${inputId}"]`);
    await text.dblclick({ position: { x: 25, y: 55 } });
    const editor = text.getByRole("textbox");
    await editor.fill(content);
    await editor.press("Escape");
    await graph(owner, doc => doc.nodes.find(node => node.id === inputId)?.metadata.content === content);
    report.original_text_editor_used = true;
}
async function checkRendered(isOwn = false) {
    const node = viewer();
    if (config.kind === "markdown") {
        await node.getByRole("heading", { name: isOwn ? "自有文档" : "车站分镜", exact: true }).waitFor({ state: "visible" });
        assert.equal(await node.locator("script, iframe, a[href^='javascript:']").count(), 0);
        assert.equal(await page.evaluate(() => window.documentNodeEscape), undefined);
        if (!isOwn) await node.getByRole("table").waitFor({ state: "visible" });
    } else if (config.kind === "svg") {
        assert.equal(await node.locator("iframe").getAttribute("sandbox"), "");
        const frame = node.frameLocator("iframe");
        await frame.locator(isOwn ? "#own-svg" : "#node-svg").waitFor({ state: "visible" });
        assert.equal(await frame.locator("html").getAttribute("data-executed"), null);
        assert.ok(!(await frame.locator("body").innerText()).includes("说明文字"));
        report.sandbox_isolation = true;
    } else if (config.kind === "html") {
        assert.equal(await node.locator("iframe").getAttribute("sandbox"), "allow-scripts");
        const frame = node.frameLocator("iframe");
        await frame.locator(isOwn ? "#template-input" : "#node-html").waitFor({ state: "visible" });
        await eventually(() => frame.locator("body").getAttribute("data-executed"), value => value === "yes");
        assert.equal(await frame.locator("body").getAttribute("data-parent-blocked"), "yes");
        assert.equal(await frame.locator("body").getAttribute("data-storage-blocked"), "yes");
        assert.equal(await page.locator("body").getAttribute("data-document-node-escape"), null);
        if (isOwn) assert.equal(await frame.locator("#template-input").innerText(), report.final_input);
        report.sandbox_isolation = true;
    } else if (config.kind === "chart") {
        await node.locator(isOwn ? ".recharts-line" : ".recharts-bar").first().waitFor({ state: "visible" });
        assert.equal(await node.locator(isOwn ? ".recharts-line" : ".recharts-bar").count(), 2);
        await node.getByText(isOwn ? "三月" : "一月", { exact: true }).waitFor({ state: "visible" });
        if (isOwn) await eventually(() => node.locator(".recharts-line-curve").evaluateAll(elements => elements.map(element => ({ dash: getComputedStyle(element).strokeDasharray, length: element.getTotalLength() }))), values => values.length === 2 && values.every(value => value.dash === "none" && value.length > 0));
        await page.waitForFunction(async () => {
            const chart = document.querySelector('[data-node-id="existing-document"] .recharts-wrapper');
            if (!chart) return false;
            let previous;
            let stable = 0;
            while (stable < 30) {
                await new Promise(requestAnimationFrame);
                const current = chart.innerHTML;
                if (!chart.querySelector(".recharts-line-curve, .recharts-bar-rectangle")) return false;
                stable = current === previous ? stable + 1 : 0;
                previous = current;
            }
            return true;
        });
    }
}
async function closeAndRead() {
    await save();
    const cookies = await owner.cookies();
    await owner.close();
    page = undefined;
    owner = await context(undefined, cookies);
    return graph();
}
async function apiFixture(document, key) {
    await read(await owner.request.post(`${origin}${path}/commits`, { headers: { "Idempotency-Key": key }, data: { expected_row_version: document.revision, source_document: document } }));
}
async function connectImage(index) {
    const source = page.locator(`[data-node-id="compare-input-${index}"]`);
    await source.click({ position: { x: 20, y: 12 } });
    await source.hover();
    const from = await source.locator('[data-canvas-connection-rail="right"]').boundingBox();
    const target = await viewer().boundingBox();
    assert.ok(from && target);
    await page.mouse.move(from.x + from.width / 2, from.y + from.height / 2);
    await page.mouse.down();
    await page.mouse.move(target.x + 1, target.y + target.height / 2, { steps: 18 });
    await page.mouse.up();
    await graph(owner, doc => doc.connections.length === index + 1);
}
async function checkCompare() {
    for (const [index, label] of ["A", "B"].entries()) {
        const image = viewer().getByRole("img", { name: label, exact: true });
        await image.waitFor({ state: "visible" });
        await eventually(() => image.evaluate(element => element.complete && element.naturalWidth > 0), Boolean);
        assert.ok((await image.getAttribute("src")).includes(config.media[index].id));
    }
}

try {
    browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH });
    owner = await context(config.username);
    await open(owner);
    report.step = "源未注册创建命令保持隐藏";
    await viewer().getByText(empty[config.kind], { exact: true }).waitFor({ state: "visible" });
    report.empty_state_seen = true;
    await page.locator("[data-canvas-viewport]").click({ button: "right", position: { x: 25, y: 150 } });
    await page.locator("[data-canvas-context-menu]").getByRole("button", { name: "添加节点", exact: true }).click();
    assert.equal(await page.getByRole("button", { name: config.title, exact: true }).count(), 0);
    await page.getByRole("button", { name: "搜索节点", exact: true }).click();
    await page.getByRole("textbox", { name: "搜索节点", exact: true }).fill(config.title);
    assert.equal(await page.getByRole("button", { name: config.title, exact: true }).count(), 0);
    await page.keyboard.press("Escape");
    await page.keyboard.press("Escape");
    report.creation_menu_absent_like_source = true;

    if (config.kind === "compare") {
        report.step = "API预置已有图片后使用原连接点";
        const before = await closeAndRead();
        before.nodes[0].position = { x: -50, y: -160 };
        before.nodes.push(...config.media.map((media, index) => ({ id: `compare-input-${index}`, type: "image", title: index ? "对比后图" : "对比前图", position: { x: -550, y: -280 + index * 360 }, width: 350, height: 180, metadata: { content: `resource:${media.id}`, storageKey: `resource:${media.id}`, mimeType: "image/png", naturalWidth: 37, naturalHeight: 19, status: "success" } })));
        await apiFixture(before, "existing-compare-media-fixture");
        await open(owner);
        await fit();
        await connectImage(0);
        await viewer().getByText("再连一张图片即可对比", { exact: true }).waitFor({ state: "visible" });
        report.single_image_empty_state = true;
        await connectImage(1);
        await checkCompare();
        report.original_inputs_linked = true;
        const host = viewer().locator('[data-canvas-no-zoom][style*="ew-resize"]');
        const bounds = await host.boundingBox();
        assert.ok(bounds);
        await page.mouse.move(bounds.x + bounds.width * 0.25, bounds.y + bounds.height * 0.65);
        await page.mouse.down();
        await page.mouse.move(bounds.x + bounds.width * 0.75, bounds.y + bounds.height * 0.65, { steps: 12 });
        await page.mouse.up();
        const clip = await viewer().getByRole("img", { name: "A", exact: true }).evaluate(image => image.style.clipPath);
        const remaining = Number(clip.match(/inset\(0(?:px)? ([\d.]+)%/)[1]);
        assert.ok(Math.abs(remaining - 25) < 1, clip);
        await page.screenshot({ path: resolve(output, "document-node-original.png") });
        report.original_viewer_rendered = true;
        const snapshot = await closeAndRead();
        assert.ok(!Object.keys(snapshot.nodes.find(node => node.id === "existing-document").metadata).some(key => /split/i.test(key)));
        await open(owner);
        await fit();
        await checkCompare();
        const restored = await viewer().getByRole("img", { name: "A", exact: true }).evaluate(image => image.style.clipPath);
        assert.match(restored, /inset\(0(?:px)? 50%/);
        report.split_is_transient = true;
    } else {
        report.step = "原输入连接点创建文字并编辑正文";
        await viewer().hover();
        await viewer().locator('[data-canvas-connection-rail="left"]').click();
        await page.locator("[data-connection-create-menu]").getByRole("button", { name: "文本", exact: true }).click();
        const connected = await graph(owner, doc => doc.nodes.length === 2 && doc.connections.length === 1);
        const text = connected.nodes.find(node => node.type === "text");
        assert.ok(text);
        inputId = text.id;
        report.input_node_id = inputId;
        assert.deepEqual(connected.connections.map(edge => [edge.fromNodeId, edge.toNodeId]), [[inputId, "existing-document"]]);
        assert.equal(text.position.x + text.width + 96, connected.nodes.find(node => node.id === "existing-document").position.x);
        await fit();
        report.original_inputs_linked = true;
        await editText(valid[config.kind]);
        await checkRendered();
        await page.screenshot({ path: resolve(output, "document-node-original.png") });
        report.original_viewer_rendered = true;
        if (config.kind === "svg" || config.kind === "chart") {
            await editText("此正文没有可渲染数据");
            await viewer().getByText(config.kind === "svg" ? "上游内容里没有找到 <svg> 源码" : "无法解析为图表数据（需要 JSON 数组或 CSV）", { exact: true }).waitFor({ state: "visible" });
            await editText(valid[config.kind]);
            await checkRendered();
            report.invalid_input_then_recovered = true;
        }
        await editText("");
        await viewer().getByText(empty[config.kind], { exact: true }).waitFor({ state: "visible" });
        report.cleared_input_empty_state = true;
        await editText(valid[config.kind]);
        await checkRendered();
        await closeAndRead();
        await open(owner);
        await fit();
        await checkRendered();
        report.cookie_only_recovery = true;

        report.step = "API预置自身内容后验证优先级与模板";
        const preset = await closeAndRead();
        const ownNode = preset.nodes.find(node => node.id === "existing-document");
        ownNode.metadata.content = own[config.kind];
        if (config.kind === "chart") ownNode.metadata.chartKind = "line";
        const upstream = preset.nodes.find(node => node.id === inputId);
        upstream.metadata.content = config.kind === "html" ? "作为模板输入的数据" : valid[config.kind];
        report.final_input = upstream.metadata.content;
        report.final_own_content = own[config.kind];
        await apiFixture(preset, "existing-document-own-content-fixture");
        await open(owner);
        await fit();
        await checkRendered(true);
        report.own_content_priority = true;
        if (config.kind === "chart") report.csv_bar_and_json_line = true;
    }
    report.step = "终态窗口恢复与成员共享查看";
    await page.screenshot({ path: resolve(output, "document-node-restored.png") });
    await closeAndRead();
    await open(owner);
    await fit();
    if (config.kind === "compare") await checkCompare();
    else await checkRendered(true);
    report.cookie_only_recovery = true;
    await save();
    await owner.close();
    page = undefined;
    report.step = "complete";
} catch (error) {
    report.error = error.stack ?? String(error);
    if (page && !page.isClosed()) await page.screenshot({ path: resolve(output, "failed.png") }).catch(() => {});
    process.exitCode = 1;
} finally {
    await writeFile(resolve(output, "report.json"), JSON.stringify(report, null, 2) + "\n");
    await browser?.close();
    await server.close();
    process.stdout.write(JSON.stringify(report) + "\n");
}
