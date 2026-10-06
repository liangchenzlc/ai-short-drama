import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { chromium } from "playwright";
import { createServer } from "vite";

// 全部成功操作通过原 UI 和真实 API；本切片不拦截请求，不调用供应商。
let input = "";
for await (const chunk of process.stdin) input += chunk;
const config = JSON.parse(input);
const origin = "http://127.0.0.1:4199";
const resources = "/api/v1/canvas-runtime/resources";
const output = resolve(".runtime/image-grid-python-browser");
const report = { step: "start", groups: [], page_errors: [], failed_routes: [] };
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

function observe(target) {
    target.on("pageerror", error => report.page_errors.push(error.message));
    target.on("response", response => {
        const path = new URL(response.url()).pathname;
        if (response.status() >= 400 && path.startsWith("/api/")) report.failed_routes.push({ path, status: response.status() });
    });
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

async function openCanvas(target, sourceKey) {
    await target.goto(`${origin}/canvas-app/canvas/${sourceKey}`);
    await target.locator("[data-canvas-viewport]").waitFor({ state: "visible" });
    await target.evaluate(() => document.fonts.ready);
}

async function graph(context, path, predicate) {
    let current;
    for (let attempt = 0; attempt < 140; attempt++) {
        current = (await read(await context.request.get(`${origin}${path}/my-document`))).source_document;
        if (predicate(current)) return current;
        await new Promise(resolve => setTimeout(resolve, 150));
    }
    assert.fail(`真实 API 宫格状态未达到预期：${JSON.stringify(current)}`);
}

async function openPicker(target, sourceId) {
    const source = target.locator(`[data-node-id="${sourceId}"]`);
    await source.waitFor({ state: "visible" });
    await source.click();
    await source.hover();
    await target.getByRole("button", { name: "图片工具", exact: true }).click();
    await target.getByRole("menuitem", { name: "宫格切分", exact: true }).click();
    const picker = target.getByRole("dialog", { name: "宫格切分", exact: true });
    await picker.waitFor({ state: "visible" });
    for (const label of ["4宫格 (2×2)", "9宫格 (3×3)", "16宫格 (4×4)", "25宫格 (5×5)"]) {
        await picker.getByRole("button", { name: label, exact: true }).waitFor({ state: "visible" });
    }
    return picker;
}

function cellFor(node) {
    const match = / · 宫格 ([1-5])-([1-5])$/.exec(node.title);
    assert.ok(match, `宫格标题含行列：${node.title}`);
    return { row: Number(match[1]) - 1, column: Number(match[2]) - 1 };
}

try {
    browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH });
    const owner = await login(config.username);
    const member = await login(config.memberUsername);
    const outsider = await login(config.outsiderUsername);
    const originalBytes = Buffer.from(config.image, "base64");
    page = await owner.newPage();
    observe(page);
    for (const scenario of config.cases) {
        report.step = `${scenario.label} 原上传与选中`;
        const path = `/api/v1/projects/${config.projectId}/canvases/${scenario.canvasId}`;
        await openCanvas(page, scenario.sourceKey);
        const upload = page.waitForResponse(response => new URL(response.url()).pathname === resources && response.request().method() === "POST");
        await page.locator('input[type="file"][accept^="image/"]').setInputFiles({ name: "grid-pixel-pattern.png", mimeType: "image/png", buffer: originalBytes });
        const sourceResource = (await read(await upload)).resource;
        const original = await graph(owner, path, document => document.nodes.length === 1 && document.nodes[0].metadata?.assetId);
        const source = original.nodes[0];
        const group = { label: scenario.label, rows: scenario.rows, columns: scenario.columns, canvas_id: scenario.canvasId, source_key: scenario.sourceKey, source_node: source, source_resource_id: sourceResource.id, pieces: [] };
        report.groups.push(group);

        if (report.groups.length === 1) {
            report.step = "自定义 1×1 拒绝和 Escape 取消";
            const beforeUploads = [];
            const record = request => {
                if (new URL(request.url()).pathname === resources && request.method() === "POST") beforeUploads.push(request.url());
            };
            page.on("request", record);
            const picker = await openPicker(page, source.id);
            await picker.getByRole("button", { name: "自定义", exact: true }).click();
            await picker.getByRole("button", { name: "1 × 1", exact: true }).hover();
            assert.equal(await picker.locator(".canvas-grid-split-custom-size").innerText(), "1 × 1");
            await picker.getByRole("button", { name: "1 × 1", exact: true }).click();
            await picker.waitFor({ state: "visible" });
            await page.keyboard.press("Escape");
            await picker.waitFor({ state: "hidden" });
            assert.equal((await graph(owner, path, document => document.nodes.length === 1)).connections.length, 0);
            assert.deepEqual(beforeUploads, []);
            page.off("request", record);
            report.cancel_and_invalid_pick_kept_source = true;
        }

        report.step = `${scenario.label} 原菜单选择并保存全组`;
        const picker = await openPicker(page, source.id);
        if (scenario.preset) {
            await picker.getByRole("button", { name: scenario.label, exact: true }).click();
        } else {
            await picker.getByRole("button", { name: "自定义", exact: true }).click();
            const cell = picker.getByRole("button", { name: `${scenario.columns} × ${scenario.rows}`, exact: true });
            await cell.hover();
            assert.equal(await picker.locator(".canvas-grid-split-custom-size").innerText(), `${scenario.columns} × ${scenario.rows}`);
            await cell.click();
        }
        const count = scenario.rows * scenario.columns;
        const saved = await graph(owner, path, document => document.nodes.length === count + 1 && document.connections.length === count && document.nodes.every(node => node.metadata?.assetId));
        const children = saved.nodes.filter(node => node.id !== source.id);
        assert.deepEqual(saved.nodes.find(node => node.id === source.id), source);
        assert.deepEqual(children.map(cellFor), Array.from({ length: count }, (_, index) => ({ row: Math.floor(index / scenario.columns), column: index % scenario.columns })));
        const sizes = children.map(node => ({ ...cellFor(node), width: node.width, height: node.height }));
        const columnWidths = Array.from({ length: scenario.columns }, (_, column) => Math.max(...sizes.filter(item => item.column === column).map(item => item.width)));
        const rowHeights = Array.from({ length: scenario.rows }, (_, row) => Math.max(...sizes.filter(item => item.row === row).map(item => item.height)));
        for (const child of children) {
            const { row, column } = cellFor(child);
            assert.equal(child.metadata.resultOrigin, "derived");
            assert.equal(child.metadata.generatedFromNodeId, source.id);
            assert.equal(child.metadata.manualSize, true);
            // 直接核对锚点、同列/同行对齐和相邻轴间距。一次 reduce 再加锚点会重排
            // IEEE754 运算，在 5×5 中产生 1ULP 差；此处保持严格等值，无坐标容差。
            const previousColumn = children.find(node => { const cell = cellFor(node); return cell.row === row && cell.column === column - 1; });
            const previousRow = children.find(node => { const cell = cellFor(node); return cell.row === row - 1 && cell.column === column; });
            const columnHead = children.find(node => { const cell = cellFor(node); return cell.row === 0 && cell.column === column; });
            const rowHead = children.find(node => { const cell = cellFor(node); return cell.row === row && cell.column === 0; });
            assert.equal(child.position.x, column === 0 ? source.position.x + source.width + 96 : previousColumn.position.x + (columnWidths[column - 1] + 48));
            assert.equal(child.position.y, row === 0 ? source.position.y : previousRow.position.y + (rowHeights[row - 1] + 48));
            assert.equal(child.position.x, columnHead.position.x);
            assert.equal(child.position.y, rowHead.position.y);
            assert.ok(saved.connections.some(edge => edge.fromNodeId === source.id && edge.toNodeId === child.id));
            assert.match(child.metadata.storageKey, /^resource:[1-9][0-9]*$/);
            const id = child.metadata.storageKey.slice("resource:".length);
            const response = await owner.request.get(`${origin}${resources}/${id}/file`);
            assert.ok(response.ok());
            const bytes = await response.body();
            group.pieces.push({ row, column, node: child, resource_id: id, asset_id: child.metadata.assetId, bytes_base64: bytes.toString("base64"), sha256: createHash("sha256").update(bytes).digest("hex") });
            assert.deepEqual(await (await member.request.get(`${origin}${resources}/${id}/file`)).body(), bytes);
            assert.equal((await member.request.get(`${origin}/api/v1/canvas-runtime/assets/${child.metadata.assetId}`)).status(), 404);
            assert.equal((await outsider.request.get(`${origin}${resources}/${id}/file`)).status(), 404);
        }
        assert.deepEqual(await (await owner.request.get(`${origin}${resources}/${sourceResource.id}/file`)).body(), originalBytes);
        assert.equal((await outsider.request.get(`${origin}${path}/my-document`)).status(), 404);
        group.saved_and_permissions_checked = true;

        report.step = `${scenario.label} 无缓存新 context 恢复`;
        const fresh = await browser.newContext({ viewport: { width: 1440, height: 900 }, reducedMotion: "no-preference", storageState: { cookies: await owner.cookies(), origins: [] } });
        fresh.setDefaultTimeout(20000);
        try {
            const restored = await fresh.newPage();
            observe(restored);
            const downloaded = new Set();
            restored.on("response", response => {
                const route = new URL(response.url()).pathname;
                if (response.ok() && route.endsWith("/file")) downloaded.add(route);
            });
            await openCanvas(restored, scenario.sourceKey);
            await restored.getByRole("button", { name: "适合屏幕", exact: true }).click();
            await restored.waitForFunction(pieces => pieces.every(piece => {
                const image = document.querySelector(`[data-node-id="${piece.node.id}"] img`);
                return image instanceof HTMLImageElement && image.complete && image.naturalWidth === piece.node.metadata.naturalWidth && image.naturalHeight === piece.node.metadata.naturalHeight;
            }), group.pieces);
            for (const piece of group.pieces) assert.ok(downloaded.has(`${resources}/${piece.resource_id}/file`), `${scenario.label} 新 context 必须真实下载每个宫格`);
            group.fresh_context_restored = true;
        } finally {
            await fresh.close();
        }
    }
    assert.equal(report.groups.length, 5);
    assert.ok(report.cancel_and_invalid_pick_kept_source);
    assert.deepEqual(report.page_errors, []);
    report.step = "complete";
} catch (error) {
    if (page) await page.screenshot({ path: resolve(output, "failure.png") }).catch(() => {});
    if (page) await writeFile(resolve(output, "failure-dom.txt"), await page.locator("body").ariaSnapshot()).catch(() => {});
    throw error;
} finally {
    await writeFile(resolve(output, "report.json"), JSON.stringify(report, null, 2) + "\n");
    await browser?.close();
    await server.close();
}
process.stdout.write(JSON.stringify(report));
