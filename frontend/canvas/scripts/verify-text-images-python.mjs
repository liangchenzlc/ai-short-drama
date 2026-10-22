import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { chromium } from "playwright";
import { createServer } from "vite";

// 原上传、连接点、生成和终态回填；真实本机 Chat/Responses/MinIO，不拦截成功请求。
let input = "";
for await (const chunk of process.stdin) input += chunk;
const config = JSON.parse(input);
const origin = "http://127.0.0.1:4199";
const path = `/api/v1/projects/${config.projectId}/canvases/${config.canvasId}`;
const resources = "/api/v1/canvas-runtime/resources";
const tasks = "/api/v1/canvas-runtime/tasks";
const mediaKind = config.mediaKind || "image";
const referenceField = mediaKind === "image" ? "referenceImages" : "referenceVideos";
const textCount = config.textCount || 1;
const effectivePrompt = config.effectivePrompt || config.prompt;
const referenceOrder = config.referenceOrder || [0, 1];
const output = resolve(config.outputDirectory || ".runtime/text-images-python-browser");
const report = { step: "start", protocol: config.protocol, media_kind: mediaKind, page_errors: [], task_post_count: 0, page_text_event_count: 0, failed_routes: [] };
const mutationRequests = new Map();
const mutationResponses = [];
report.mutation_trace = [];
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
    target.on("request", request => {
        const route = new URL(request.url()).pathname;
        if (request.method() === "POST" && (route === `${path}/commits` || route === "/api/v1/canvas-runtime/ops/canvas.task.bind")) {
            const body = request.postDataJSON();
            const entry = { kind: route.endsWith("/commits") ? "commit" : "bind", step: report.step, expected_revision: body.expected_row_version, task_id: body.params?.taskId, nodes: body.source_document?.nodes.map(node => ({ id: node.id, status: node.metadata?.status, task_id: node.metadata?.taskId })) };
            mutationRequests.set(request, entry);
            report.mutation_trace.push(entry);
        }
        if (route === tasks && request.method() === "POST") report.task_post_count++;
        if (route.startsWith(`${tasks}/`) && route.endsWith("/text-events")) report.page_text_event_count++;
    });
    target.on("response", response => {
        const route = new URL(response.url()).pathname;
        if (response.status() >= 400 && route.startsWith("/api/")) report.failed_routes.push({ path: route, status: response.status() });
        const entry = mutationRequests.get(response.request());
        if (entry) mutationResponses.push(response.json().then(body => {
            entry.status = response.status();
            entry.revision = body.row_version ?? body.result?.revision;
            entry.current_version = body.error?.details?.current_version;
            entry.error_code = body.error?.code;
        }));
    });
}

async function login(username, cookies) {
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, reducedMotion: "no-preference", ...(cookies ? { storageState: { cookies, origins: [] } } : {}) });
    context.setDefaultTimeout(20000);
    context.setDefaultNavigationTimeout(60000);
    if (!cookies) await read(await context.request.post(`${origin}/api/v1/auth/login`, { headers: { Origin: origin }, data: { username, password: config.password } }));
    const csrf = (await context.cookies()).find(cookie => cookie.name === "sd_csrf");
    assert.ok(csrf);
    await context.setExtraHTTPHeaders({ Origin: origin, "X-CSRF-Token": csrf.value });
    return context;
}

async function graph(context, predicate) {
    let current;
    for (let attempt = 0; attempt < 120; attempt++) {
        current = (await read(await context.request.get(`${origin}${path}/my-document`))).source_document;
        if (predicate(current)) return current;
        await new Promise(resolve => setTimeout(resolve, 100));
    }
    assert.fail(`真实 API 文字画布没有达到预期：${JSON.stringify(current)}`);
}

async function openCanvas(target) {
    await target.goto(`${origin}/canvas-app/canvas/${config.sourceKey}`);
    await target.locator("[data-canvas-viewport]").waitFor({ state: "visible" });
    await target.evaluate(() => document.fonts.ready);
}

async function fitToScreen(target) {
    await target.getByRole("button", { name: "适合屏幕", exact: true }).click();
    await target.evaluate(() => new Promise((done, fail) => {
        let previous;
        let stable = 0;
        const started = performance.now();
        const sample = () => {
            const boxes = JSON.stringify([...document.querySelectorAll("[data-node-id]")].map(node => {
                const { x, y, width, height } = node.getBoundingClientRect();
                return [x, y, width, height];
            }));
            stable = boxes === previous ? stable + 1 : 0;
            previous = boxes;
            if (stable >= 4) done();
            else if (performance.now() - started > 5000) fail(new Error("适合屏幕后节点几何未稳定"));
            else requestAnimationFrame(sample);
        };
        requestAnimationFrame(sample);
    }));
}

async function firstDraft(context, taskId) {
    let current;
    for (let attempt = 0; attempt < 70; attempt++) {
        current = await read(await context.request.get(`${origin}${tasks}/${taskId}/text-deltas`));
        if (current.textDraft === config.first) return current;
        await new Promise(resolve => setTimeout(resolve, 50));
    }
    assert.fail(`真实供应商第一段未持久化：${JSON.stringify(current)}`);
}

async function selectVideoNode(target, node) {
    // 适合屏幕按钮属于 data-canvas-no-zoom；其聚焦时源控制器忽略 Escape。
    const blank = await target.evaluate(() => [[1200, 700], [240, 700], [1200, 80], [720, 80]].find(([x, y]) => {
        const hit = document.elementFromPoint(x, y);
        return hit && !hit.closest("[data-node-id], [data-canvas-no-zoom]");
    }));
    assert.ok(blank, "适合屏幕后应有可点击的画布空白处");
    await target.mouse.click(...blank);
    await target.keyboard.press("Escape");
    await target.waitForFunction(() => document.querySelectorAll('[data-node-id][data-node-selected="true"]').length === 0);
    await node.locator(".canvas-node-external-header > div > svg").click();
    assert.equal(await target.locator('[data-node-id][data-node-selected="true"]').count(), 1);
    assert.equal(await node.getAttribute("data-node-selected"), "true");
}

async function save(target) {
    const live = await target.locator("[data-canvas-viewport]").evaluate(viewport => ({
        x: parseFloat(viewport.style.getPropertyValue("--canvas-live-x")),
        y: parseFloat(viewport.style.getPropertyValue("--canvas-live-y")),
        k: parseFloat(viewport.style.getPropertyValue("--canvas-live-scale")),
    }));
    await graph(target.context(), document => document.viewport.x === live.x && document.viewport.y === live.y && document.viewport.k === live.k);
    await target.getByRole("button", { name: /^画布保存状态：/ }).press("Control+s");
    // Toast 由既有样式隐藏；检查保存完成后挂载及可见顶栏状态。
    await target.locator(".ant-message").getByText(/^画布已保存(?:到云端)?$/, { exact: true }).waitFor({ state: "attached" });
    await target.getByRole("button", { name: "画布保存状态：已保存", exact: true }).waitFor({ state: "visible" });
}

try {
    browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH });
    const owner = await login(config.username);
    const member = await login(config.memberUsername);
    const outsider = await login(config.outsiderUsername);
    page = await owner.newPage();
    observe(page);
    await openCanvas(page);
    report.step = "原上传 input 创建两个媒体节点";
    const originals = config.images.map(value => Buffer.from(value, "base64"));
    await page.locator('input[type="file"][accept^="image/"]').setInputFiles(originals.map((buffer, index) => ({
        name: `${index === 0 ? "red" : "blue"}-reference.${mediaKind === "image" ? "png" : "mp4"}`, mimeType: mediaKind === "image" ? "image/png" : "video/mp4", buffer,
    })));
    let uploaded = await graph(owner, document => document.nodes.length === 2 && document.nodes.every(node => node.metadata?.assetId && node.metadata.storageKey?.startsWith("resource:")));
    await fitToScreen(page);
    if (mediaKind === "video") {
        uploaded = await graph(owner, document => document.nodes.length === 2 && document.nodes.every(node => node.metadata?.videoPreview?.captureVersion === 2 && node.metadata.videoPreview.sourceKey === node.metadata.storageKey));
        report.source_video_previews_hydrated = true;
    }
    report.media = [];
    for (const [index, title] of ["red-reference", "blue-reference"].entries()) {
        const source = uploaded.nodes.find(node => node.title.includes(title));
        assert.ok(source, `原上传应保留文件标题 ${title}`);
        const resourceId = source.metadata.storageKey.slice("resource:".length);
        const response = await owner.request.get(`${origin}${resources}/${resourceId}/file`);
        assert.ok(response.ok());
        assert.deepEqual(await response.body(), originals[index]);
        report.media.push({ node_id: source.id, resource_id: resourceId, asset_id: source.metadata.assetId, sha256: createHash("sha256").update(originals[index]).digest("hex") });
    }
    const [red, blue] = report.media.map(item => uploaded.nodes.find(node => node.id === item.node_id));

    report.step = "原输出连接点创建文字";
    const redNode = page.locator(`[data-node-id="${red.id}"]`);
    if (mediaKind === "video") await selectVideoNode(page, redNode);
    else await redNode.click();
    await redNode.hover();
    await redNode.locator('[data-canvas-connection-rail="right"]').click();
    const create = page.locator("[data-connection-create-menu]");
    await create.waitFor({ state: "visible" });
    await create.getByRole("button", { name: "文本", exact: true }).click();
    const connected = await graph(owner, document => document.nodes.length === 3 && document.connections.length === 1);
    const text = connected.nodes.find(node => node.type === "text");
    assert.ok(text);
    assert.deepEqual(connected.connections.map(edge => [edge.fromNodeId, edge.toNodeId]), [[red.id, text.id]]);
    assert.equal(text.position.x, red.position.x + red.width + 96);
    assert.ok(!text.metadata?.content);
    report.text_node_id = text.id;
    report.original_connection_created_text = true;

    report.step = "第二个媒体经原连接点拖拽入文字";
    await fitToScreen(page);
    const blueNode = page.locator(`[data-node-id="${blue.id}"]`);
    const textNode = page.locator(`[data-node-id="${text.id}"]`);
    if (mediaKind === "video") await selectVideoNode(page, blueNode);
    else await blueNode.click();
    await blueNode.hover();
    const from = await blueNode.locator('[data-canvas-connection-rail="right"]').boundingBox();
    const target = await textNode.boundingBox();
    assert.ok(from && target);
    await page.mouse.move(from.x + from.width / 2, from.y + from.height / 2);
    await page.mouse.down();
    await page.mouse.move(target.x + 1, target.y + target.height / 2, { steps: 18 });
    await page.mouse.up();
    const linked = await graph(owner, document => document.connections.length === 2);
    assert.deepEqual(linked.connections.map(edge => [edge.fromNodeId, edge.toNodeId]), [[red.id, text.id], [blue.id, text.id]]);
    report.original_media_order_linked = true;

    report.step = "原生成按钮提交媒体文字任务";
    await textNode.click({ position: { x: 20, y: 12 } });
    const prompt = page.getByRole("textbox", { name: "文本提示词", exact: true });
    await prompt.waitFor({ state: "visible" });
    const count = page.getByRole("spinbutton", { name: "文本生成份数", exact: true });
    assert.equal(await count.inputValue(), "1");
    if (textCount > 1) {
        for (const [entered, expected] of [["0", 1], ["16", 15], [String(textCount), textCount]]) {
            await count.fill(entered);
            await count.press("Tab");
            assert.equal(await count.inputValue(), String(expected));
            await graph(owner, document => (document.nodes.find(node => node.id === text.id)?.metadata.textCount ?? 1) === expected);
        }
        report.text_count_bounds_verified = true;
    }
    if (config.verifyMissingMention) {
        await prompt.fill("描述 @图片99。");
        await page.getByRole("button", { name: "生成", exact: true }).click();
        await page.locator(".ant-message").getByText(/@图片99 没有对应的画布资源/).waitFor({ state: "attached" });
        assert.equal(report.task_post_count, 0);
        const rejected = await read(await owner.request.get(`${origin}${path}/my-document`));
        assert.equal(rejected.source_document.nodes.length, 3);
        assert.equal(rejected.source_document.connections.length, 2);
        report.missing_mention_rejected = true;
    }
    await prompt.fill(config.prompt);
    const admissions = Array.from({ length: textCount }, (_, batchIndex) => page.waitForResponse(response =>
        new URL(response.url()).pathname === tasks && response.request().method() === "POST"
        && response.request().postDataJSON().input.metadata.batchIndex === batchIndex,
    ));
    const [responses] = await Promise.all([
        Promise.all(admissions),
        page.getByRole("button", { name: "生成", exact: true }).click(),
    ]);
    const admittedTasks = await Promise.all(responses.map(read));
    const task = admittedTasks[0];
    report.task_id = task.id;
    assert.equal(task.clientContext.nodeId, text.id);
    report.tasks = admittedTasks.map((item, batchIndex) => {
        const declared = JSON.parse(item.inputJson);
        assert.equal(declared.prompt, effectivePrompt);
        assert.equal(declared.config.systemPrompt, config.systemPrompt);
        assert.deepEqual(declared.textOptions, { stream: true, thinking: false });
        assert.equal(declared.metadata.batchIndex, batchIndex);
        assert.equal(declared.metadata.batchCount, textCount);
        assert.deepEqual(declared[referenceField].map(item => item.storageKey), referenceOrder.map(index => `resource:${report.media[index].resource_id}`));
        return { task_id: item.id, target_node_id: item.clientContext.nodeId, batch_index: batchIndex, client_operation_id: item.clientOperationId };
    });
    assert.equal(new Set(report.tasks.map(item => item.task_id)).size, textCount);
    assert.equal(new Set(report.tasks.map(item => item.target_node_id)).size, textCount);
    if (textCount > 1) {
        const operation = report.tasks[0].client_operation_id.slice(0, -2);
        report.tasks.forEach((item, index) => assert.equal(item.client_operation_id, `${operation}:${index}`));
    }
    const admittedGraph = await graph(owner, document => report.tasks.every(item => document.nodes.some(node => node.id === item.target_node_id && node.metadata.taskId === item.task_id)));
    assert.equal(admittedGraph.nodes.length, 2 + textCount);
    assert.equal(admittedGraph.connections.length, 1 + textCount);
    for (const [index, item] of report.tasks.entries()) {
        const target = admittedGraph.nodes.find(node => node.id === item.target_node_id);
        assert.equal(target.metadata.prompt, effectivePrompt);
        assert.equal(target.metadata.composerContent, config.prompt);
        if (index) {
            assert.equal(target.position.x, text.position.x + text.width + 96);
            assert.equal(target.position.y, text.position.y + text.height / 2 - target.height / 2 + (index - 1 - (textCount - 2) / 2) * (target.height + 36));
            assert.equal(target.width, text.width);
            assert.equal(target.height, text.height);
            assert.ok(admittedGraph.connections.some(edge => edge.fromNodeId === text.id && edge.toNodeId === target.id));
        }
    }
    report.explicit_references_verified = Boolean(config.verifyMissingMention);
    report.text_batch_layout_verified = textCount > 1;
    report.source_saved_before_admission = true;

    report.step = "供应商第一段已落库而原节点仍保持加载态";
    const firstTask = [...admittedTasks].sort((left, right) => BigInt(left.id) < BigInt(right.id) ? -1 : 1)[0];
    const draft = await firstDraft(owner, firstTask.id);
    assert.equal(draft.status, "running");
    assert.equal(draft.complete, false);
    assert.deepEqual(draft.deltas.map(item => [item.sequence, item.content]), [[1, config.first]]);
    const inProgress = await read(await owner.request.get(`${origin}${path}/my-document`));
    for (const item of report.tasks) {
        const loading = inProgress.source_document.nodes.find(node => node.id === item.target_node_id);
        assert.equal(loading.metadata.status, "loading");
        assert.ok(!loading.metadata.content);
        assert.ok(!(await page.locator(`[data-node-id="${item.target_node_id}"]`).innerText()).includes(config.first));
    }
    assert.equal(report.page_text_event_count, 0, "源普通文字节点不请求实时正文 SSE");
    assert.equal((await member.request.get(`${origin}${tasks}/${firstTask.id}/text-deltas`)).status(), 404);
    report.first_draft = { text: draft.textDraft, sequences: draft.deltas.map(item => item.sequence) };
    report.private_delta_kept_original_loading = true;
    await page.screenshot({ path: resolve(output, "loading.png") });
    const bindings = report.tasks.map(item => page.waitForResponse(response =>
        new URL(response.url()).pathname === "/api/v1/canvas-runtime/ops/canvas.task.bind"
        && response.request().method() === "POST" && response.ok()
        && response.request().postDataJSON().params.taskId === item.task_id,
    ));
    await writeFile(config.releasePath, "Browser observed the actual private provider delta and the original loading node.\n");

    report.step = "原消费者终态直接回填";
    const boundTasks = await Promise.all((await Promise.all(bindings)).map(read));
    boundTasks.forEach((bound, index) => {
        assert.equal(bound.result.taskId, report.tasks[index].task_id);
        assert.equal(bound.result.node.id, report.tasks[index].target_node_id);
        assert.equal(bound.result.node.metadata.content, config.first + config.second);
    });
    await fitToScreen(page);
    await textNode.getByText(config.first + config.second, { exact: true }).waitFor({ state: "visible" });
    const final = await graph(owner, document => report.tasks.every(item => document.nodes.find(node => node.id === item.target_node_id)?.metadata.content === config.first + config.second));
    assert.equal(final.nodes.length, 2 + textCount);
    assert.equal(final.connections.length, 1 + textCount);
    assert.deepEqual(final.nodes.filter(node => node.type === mediaKind), uploaded.nodes);
    report.original_media_unchanged = true;
    report.original_consumer_bound_same_node = true;

    report.step = "全新 context 恢复正文与 MinIO 原媒体";
    await save(page);
    const cookies = await owner.cookies();
    await owner.close();
    page = undefined;
    report.saved_and_owner_closed = true;
    const fresh = await login(undefined, cookies);
    const restored = await fresh.newPage();
    page = restored;
    observe(restored);
    const downloaded = new Set();
    restored.on("response", response => {
        const route = new URL(response.url()).pathname;
        if (response.ok() && route.endsWith("/file")) downloaded.add(route);
    });
    await openCanvas(restored);
    await fitToScreen(restored);
    for (const item of report.tasks) await restored.locator(`[data-node-id="${item.target_node_id}"]`).getByText(config.first + config.second, { exact: true }).waitFor({ state: "visible" });
    if (mediaKind === "image") {
        await restored.waitForFunction(items => items.every(item => {
            const image = document.querySelector(`[data-node-id="${item.node_id}"] img`);
            return image instanceof HTMLImageElement && image.complete && image.naturalWidth === 37 && image.naturalHeight === 19;
        }), report.media);
    } else {
        for (const item of report.media) {
            const node = restored.locator(`[data-node-id="${item.node_id}"]`);
            await node.getByRole("button", { name: /^播放 / }).first().click();
            const video = node.locator("video").first();
            await video.waitFor({ state: "visible" });
            await video.evaluate(async media => {
                if (media.readyState >= 1) return;
                await new Promise((resolve, reject) => {
                    media.addEventListener("loadedmetadata", resolve, { once: true });
                    media.addEventListener("error", () => reject(new Error("已归档参考视频无法解码")), { once: true });
                });
            });
            const measured = await video.evaluate(media => ({ width: media.videoWidth, height: media.videoHeight, duration: media.duration }));
            assert.deepEqual(measured, { width: 160, height: 90, duration: 1 });
        }
    }
    report.restored_media_decoded = true;
    for (const media of report.media) assert.ok(downloaded.has(`${resources}/${media.resource_id}/file`));
    await save(restored);
    await restored.screenshot({ path: resolve(output, "restored.png") });
    report.fresh_context_restored = true;

    report.step = "共享最终正文与私人任务权限";
    assert.equal((await member.request.get(`${origin}${path}/my-document`)).status(), 404);
    const shared = await read(await fresh.request.get(`${origin}${path}`));
    for (const item of report.tasks) {
        assert.equal(shared.source_document.nodes.find(node => node.id === item.target_node_id).metadata.content, config.first + config.second);
        assert.equal((await member.request.get(`${origin}${tasks}/${item.task_id}`)).status(), 404);
        assert.equal((await member.request.get(`${origin}${tasks}/${item.task_id}/text-deltas`)).status(), 404);
    }
    assert.equal((await outsider.request.get(`${origin}${path}/my-document`)).status(), 404);
    for (const [index, image] of report.media.entries()) {
        assert.equal((await member.request.get(`${origin}${resources}/${image.resource_id}/file`)).status(), 404);
        assert.equal((await member.request.get(`${origin}/api/v1/canvas-runtime/assets/${image.asset_id}`)).status(), 404);
        assert.equal((await outsider.request.get(`${origin}${resources}/${image.resource_id}/file`)).status(), 404);
    }
    report.permissions_checked = true;
    assert.equal(report.task_post_count, textCount);
    assert.equal(report.page_text_event_count, 0);
    await Promise.all(mutationResponses);
    assert.deepEqual(report.page_errors, []);
    assert.deepEqual(report.failed_routes, []);
    report.step = "complete";
} catch (error) {
    report.failure_stack = error.stack;
    if (page) await page.screenshot({ path: resolve(output, "failure.png") }).catch(() => {});
    if (page) await writeFile(resolve(output, "failure-dom.txt"), await page.locator("body").ariaSnapshot()).catch(() => {});
    throw error;
} finally {
    await Promise.allSettled(mutationResponses);
    await writeFile(resolve(output, "report.json"), JSON.stringify(report, null, 2) + "\n");
    await browser?.close();
    await server.close();
}
process.stdout.write(JSON.stringify(report));
