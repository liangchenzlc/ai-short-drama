import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { chromium } from "playwright";
import { createServer } from "vite";

let input = "";
for await (const chunk of process.stdin) input += chunk;
const config = JSON.parse(input);
const origin = `http://127.0.0.1:${config.port}`;
const output = resolve(config.output);
const report = {
    viewport_width: config.width, menus: [], normalizations: [], commits: [],
    page_errors: [], http_errors: [], external_requests: [], provider_requests: 0,
    cleanup_errors: [], diagnostic_errors: [],
    provider_submission_requests: [], observed_generation_history_reads: [],
    legacy_source_project_http_errors: [],
    binding: { source_project_id: config.sourceProjectId, owning_project_id: config.owningProjectId,
        canvas_id: config.canvasId, source_key: config.sourceKey },
    resources: { node_pid: process.pid, vite_port: config.port },
    original_details: false, cookie_only_restored: false,
    member_work_visible: false, private_isolated: false,
};
await mkdir(output, { recursive: true });
let server;
let browserServer;
let browser;
let page;
const pendingResponses = [];
const contexts = new Set();

async function read(response) {
    assert.ok(response.ok(), `HTTP ${response.status()} ${new URL(response.url()).pathname}: ${await response.text()}`);
    return response.json();
}
function responseWait(predicate) {
    const pending = page.waitForResponse(predicate);
    // Selection may fail before this response is awaited; retain the rejection for that await.
    pending.catch(() => {});
    return pending;
}
async function closeOwned(name, close) {
    try {
        await close();
        report.resources[`${name}_closed`] = true;
    } catch (error) {
        report.cleanup_errors.push({ resource: name, error: error.stack || String(error) });
        process.exitCode = 1;
    }
}
function observe(observed) {
    observed.on("pageerror", error => report.page_errors.push(error.message));
    observed.on("request", request => {
        const url = new URL(request.url());
        if (!["http:", "https:"].includes(url.protocol)) return;
        if (url.origin !== origin) report.external_requests.push(`${url.origin}${url.pathname}`);
        const method = request.method();
        if (method === "GET" && url.pathname === "/api/v1/ai/generations") {
            report.observed_generation_history_reads.push({ method, path: url.pathname });
        }
        const submission = method === "POST" && [
            /^\/api\/v1\/canvas-runtime\/tasks(?:$|\/[0-9]+\/(?:retry|resume)$)/,
            /^\/api\/v1\/ai\/generations\/(?:text|image|video|audio|[0-9]+\/(?:retry|resume))$/,
            /^\/api\/v1\/assistant\/conversations\/[0-9]+\/messages$/,
            /^\/api\/v1\/canvas-runtime\/creation-runs\/[0-9]+\/execute$/,
        ].some(pattern => pattern.test(url.pathname));
        if (submission) {
            report.provider_requests++;
            report.provider_submission_requests.push({ method, path: url.pathname });
        }
        if (url.pathname === `${config.documentPath}/commits` && request.method() === "POST") {
            const body = request.postDataJSON();
            report.commits.push({ key: request.headers()["idempotency-key"], expected: body.expected_row_version,
                node_ids: body.source_document.nodes.map(node => node.id),
                covers: body.source_document.nodes.map(node => node.metadata?.characterCoverUrl) });
        }
    });
    observed.on("response", response => {
        const path = new URL(response.url()).pathname;
        if (response.status() >= 400 && path.startsWith("/api/")) {
            const entry = { method: response.request().method(), path, status: response.status() };
            report.http_errors.push(entry);
            pendingResponses.push(response.json().then(body => { entry.code = body.error?.code; }).catch(() => {}));
        }
    });
}
async function context(cookies) {
    const value = await browser.newContext({ viewport: { width: config.width, height: 900 },
        reducedMotion: "no-preference", ...(cookies ? { storageState: { cookies, origins: [] } } : {}) });
    value.setDefaultTimeout(20000);
    contexts.add(value);
    return value;
}
async function login(value, username) {
    await read(await value.request.post(`${origin}/api/v1/auth/login`, {
        headers: { Origin: origin }, data: { username, password: config.password },
    }));
    const csrf = (await value.cookies()).find(cookie => cookie.name === "sd_csrf");
    assert.ok(csrf?.value);
    await value.setExtraHTTPHeaders({ Origin: origin, "X-CSRF-Token": csrf.value });
}
async function graph(value, predicate = () => true) {
    const deadline = Date.now() + 30000;
    let result;
    do {
        result = await read(await value.request.get(`${origin}${config.documentPath}/my-document`));
        if (predicate(result.source_document)) return result;
        await new Promise(resolve => setTimeout(resolve, 150));
    } while (Date.now() < deadline);
    assert.fail(`真实自动保存未达到预期状态：${JSON.stringify({ result, commits: report.commits, http_errors: report.http_errors })}`);
}
async function open(value) {
    page = await value.newPage();
    observe(page);
    await page.goto(`${origin}/canvas-app/canvas/${config.sourceKey}?libtvChrome=1`, { timeout: 60000 });
    await page.locator("[data-canvas-viewport]").waitFor();
    if (config.width < 1024) await page.getByRole("button", { name: "退出专注模式", exact: true }).click();
}
async function file(value, id) {
    const response = await value.request.get(`${origin}/api/v1/canvas-runtime/resources/${id}/file`);
    assert.ok(response.ok(), `真实封面文件读取失败：HTTP ${response.status()}`);
    const body = await response.body();
    assert.equal(createHash("sha256").update(body).digest("hex"), config.imageSha256);
    return body;
}
async function originalDetail(node) {
    await page.getByRole("button", { name: "适合屏幕", exact: true }).click();
    await page.locator(`[data-node-id="${node.id}"]`).dblclick();
    await page.getByText("项目角色引用 · 当前版本", { exact: true }).waitFor();
    await page.getByRole("heading", { name: "阿青", exact: true }).waitFor();
    const image = page.getByRole("img", { name: "阿青人物三视图", exact: true });
    await image.waitFor();
    assert.equal(await image.getAttribute("src"), node.metadata.characterCoverUrl);
    await page.waitForFunction(() => {
        const image = document.querySelector('img[alt="阿青人物三视图"]');
        return image instanceof HTMLImageElement && image.complete && image.naturalWidth === 37 && image.naturalHeight === 19;
    });
    await page.screenshot({ path: resolve(output, `detail-${report.step}.png`) });
    await page.locator(".ant-modal-close").click();
}
async function selectCharacter(entry) {
    let placement;
    if (entry === "studio") await page.getByRole("button", { name: "人像造型室", exact: true }).click();
    else if (entry === "add") {
        await page.getByRole("button", { name: "添加节点", exact: true }).click();
        await page.getByRole("button", { name: "添加角色卡", exact: true }).click();
    } else {
        const viewport = await page.locator("[data-canvas-viewport]").boundingBox();
        const transform = await page.evaluate(async key => {
            const { useCanvasStore } = await import("/canvas-app/src/stores/canvas/use-canvas-store.ts");
            return structuredClone(useCanvasStore.getState().projects.find(item => item.id === key).viewport);
        }, config.sourceKey);
        const click = { x: viewport.width - 70, y: 250 };
        placement = { x: (click.x - transform.x) / transform.k, y: (click.y - transform.y) / transform.k };
        await page.mouse.click(viewport.x + click.x, viewport.y + click.y, { button: "right" });
        await page.locator("[data-canvas-context-menu]").getByRole("button", { name: "添加节点", exact: true }).click();
        await page.getByRole("button", { name: "添加角色卡", exact: true }).click();
    }
    const choice = page.locator(".asset-picker-card-action[title='阿青']");
    await choice.waitFor();
    await choice.click();
    await page.getByRole("button", { name: "引入已选资产（1）", exact: true }).click();
    await choice.waitFor({ state: "hidden" });
    return placement;
}

try {
    server = await createServer({ cacheDir: resolve(output, "vite-cache"), server: { host: "127.0.0.1", port: config.port,
        strictPort: true, proxy: { "/api": { target: config.apiUrl, changeOrigin: true } } }, logLevel: "silent" });
    await server.listen();
    browserServer = await chromium.launchServer({ headless: true, executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH });
    report.resources.chromium_pid = browserServer.process().pid;
    report.resources.browser_server_port = Number(new URL(browserServer.wsEndpoint()).port);
    browser = await chromium.connect(browserServer.wsEndpoint());
    let owner = await context();
    await login(owner, config.username);
    assert.deepEqual((await graph(owner)).source_document.nodes, []);
    await open(owner);
    let copied;
    for (const [index, entry] of ["studio", "add", "context"].entries()) {
        report.step = entry;
        const normalized = index === 0 ? responseWait(response => {
            if (new URL(response.url()).pathname !== "/api/v1/canvas-runtime/resources/normalize" || response.request().method() !== "POST") return false;
            return response.request().postDataJSON()?.resource_ids.includes(config.sourceMediaId);
        }) : null;
        const acknowledgement = responseWait(response => {
            if (new URL(response.url()).pathname !== `${config.documentPath}/commits` || response.request().method() !== "POST") return false;
            const nodes = response.request().postDataJSON()?.source_document?.nodes || [];
            return nodes.filter(node => node.metadata?.characterAssetId === config.assetId).length === index + 1
                && nodes.every(node => node.metadata?.characterCoverUrl && !node.metadata.characterCoverUrl.includes(`/${config.sourceMediaId}/`));
        });
        const placement = await selectCharacter(entry);
        if (normalized) {
            const response = await read(await normalized);
            copied = response.resource_map[config.sourceMediaId];
            assert.ok(copied && copied !== config.sourceMediaId);
            assert.ok(response.resource_aliases[copied].includes(config.sourceMediaId));
            report.normalizations.push({ original: config.sourceMediaId, copied, aliases: response.resource_aliases[copied] });
        }
        const ack = await read(await acknowledgement);
        const saved = await graph(owner, document => document.nodes.length === index + 1);
        assert.ok(BigInt(saved.row_version) >= BigInt(ack.row_version));
        const node = saved.source_document.nodes.find(node => !report.node_ids?.includes(node.id));
        assert.ok(node);
        assert.equal(node.type, "text"); assert.equal(node.width, 320); assert.equal(node.height, 260);
        assert.equal(node.metadata.characterAssetId, config.assetId);
        assert.equal(node.metadata.characterVersionId, config.versionId);
        assert.equal(node.metadata.characterVersionPolicy, "current");
        assert.equal(node.metadata.characterCoverUrl, `/api/v1/canvas-runtime/resources/${copied}/file`);
        assert.deepEqual(node.metadata.characterDefinition, { description: "青衣少女", prompt: "保持青衣" });
        assert.equal(node.metadata.characterPrompt, "【角色卡：阿青】\n青衣少女\n保持青衣");
        if (placement) {
            assert.ok(Math.abs(node.position.x + node.width / 2 - placement.x) < 1);
            assert.ok(Math.abs(node.position.y + node.height / 2 - placement.y) < 1);
        }
        (report.node_ids ||= []).push(node.id);
        report.menus.push(entry);
        await originalDetail(node);
    }
    report.original_details = true;
    report.copy_id = copied;
    assert.deepEqual(await file(owner, copied), await file(owner, config.sourceMediaId));
    const cookies = await owner.cookies();
    await owner.close(); contexts.delete(owner);
    report.resources.owner_original_closed = true;
    owner = await context(cookies);
    const csrf = cookies.find(cookie => cookie.name === "sd_csrf");
    await owner.setExtraHTTPHeaders({ Origin: origin, "X-CSRF-Token": csrf.value });
    const beforePrivate = await graph(owner);
    const privateDocument = structuredClone(beforePrivate.source_document);
    Object.assign(privateDocument.nodes[0].metadata, {
        model: "owner-private-model", characterPrompt: "owner-private-prompt", characterVoiceName: "owner-private-voice",
    });
    await read(await owner.request.post(`${origin}${config.documentPath}/commits`, {
        headers: { "Idempotency-Key": `role-private-projection-${config.width}` },
        data: { expected_row_version: beforePrivate.row_version, schema_version: 1, source_document: privateDocument },
    }));
    report.step = "owner-fresh";
    await open(owner);
    const restored = await graph(owner, document => document.nodes[0]?.metadata.model === "owner-private-model");
    assert.deepEqual(restored.source_document.nodes.map(node => node.id), report.node_ids);
    assert.ok(restored.source_document.nodes.every(node => node.metadata.characterCoverUrl === `/api/v1/canvas-runtime/resources/${copied}/file`));
    await originalDetail(restored.source_document.nodes.at(-1));
    assert.ok((await read(await owner.request.get(`${origin}/api/v1/canvas-runtime/assets`))).assets.some(asset => asset.id === config.privateAssetId));
    report.cookie_only_restored = true;
    const member = await context();
    await login(member, config.memberUsername);
    const memberGraph = await graph(member);
    assert.deepEqual(memberGraph.source_document.nodes.map(node => node.id), report.node_ids);
    assert.ok(!JSON.stringify(memberGraph.source_document).includes("owner-private"));
    assert.ok(memberGraph.source_document.nodes.every(node => node.metadata.characterAssetId === config.assetId
        && node.metadata.characterVersionId === config.versionId && node.metadata.characterCoverUrl === `/api/v1/canvas-runtime/resources/${copied}/file`));
    assert.deepEqual(await file(member, copied), await file(owner, copied));
    assert.ok(!(await read(await member.request.get(`${origin}/api/v1/canvas-runtime/assets`))).assets.some(asset => asset.id === config.privateAssetId));
    report.step = "member";
    await open(member);
    await originalDetail(memberGraph.source_document.nodes.at(-1));
    const afterMember = await graph(member);
    assert.ok(!JSON.stringify(afterMember.source_document).includes("owner-private"));
    const authorAgain = await graph(owner);
    assert.equal(authorAgain.source_document.nodes[0].metadata.model, "owner-private-model");
    assert.equal(authorAgain.source_document.nodes[0].metadata.characterPrompt, "owner-private-prompt");
    assert.equal(authorAgain.source_document.nodes[0].metadata.characterVoiceName, "owner-private-voice");
    report.member_work_visible = true;
    report.private_isolated = true;
    await Promise.all(pendingResponses);
    assert.deepEqual(report.page_errors, []);
    const legacySourcePath = `/api/v1/canvas-runtime/projects/${config.sourceProjectId}`;
    const isLegacySourceRead = error => error.method === "GET" && error.path === legacySourcePath && error.status === 404;
    report.legacy_source_project_http_errors = report.http_errors.filter(isLegacySourceRead);
    assert.deepEqual(report.http_errors.filter(error => !isLegacySourceRead(error)), []);
    assert.deepEqual(report.external_requests, []);
    assert.equal(report.provider_requests, 0);
    report.step = "complete";
} catch (error) {
    report.error = error.stack || String(error);
    process.exitCode = 1;
    if (page && !page.isClosed()) {
        try {
            await page.screenshot({ path: resolve(output, "failed.png") });
        } catch (diagnostic) {
            report.diagnostic_errors.push({ artifact: "failed.png", error: diagnostic.stack || String(diagnostic) });
        }
        try {
            await writeFile(resolve(output, "failed-dom.txt"), await page.locator("body").ariaSnapshot());
        } catch (diagnostic) {
            report.diagnostic_errors.push({ artifact: "failed-dom.txt", error: diagnostic.stack || String(diagnostic) });
        }
    }
} finally {
    let index = 0;
    for (const value of contexts) await closeOwned(`context_${index++}`, () => value.close());
    if (browser) await closeOwned("browser_connection", () => browser.close());
    if (browserServer) await closeOwned("browser_server", () => browserServer.close());
    if (server) await closeOwned("vite", () => server.close());
    await Promise.all(pendingResponses);
    await writeFile(resolve(output, "report.json"), JSON.stringify(report, null, 2) + "\n");
    process.stdout.write(JSON.stringify(report) + "\n");
}
