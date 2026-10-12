import assert from "node:assert/strict";
import { realpathSync } from "node:fs";
import { mkdir } from "node:fs/promises";
import { resolve } from "node:path";
import test from "node:test";
import { chromium } from "playwright";
import { createServer } from "vite";

const testCacheDir = resolve(".runtime/vite-e2e-character-current");

// These fixtures verify the real editor and HTTP adapter, not providers or Python persistence.
const initial = {
    id: "parity-canvas", workspaceProjectId: "9007199254740995", projectId: "9100000000000002", revision: "1",
    title: "迁移对照", canvasTitle: "主画布", createdAt: "2026-01-01T00:00:00Z", updatedAt: "2026-01-01T00:00:00Z",
    nodes: [{ id: "text-one", type: "text", title: "文字节点", position: { x: 420, y: 260 }, width: 320, height: 220, metadata: { content: "画布迁移操作验证" } }],
    connections: [], chatSessions: [], activeChatId: null, directorScenes: [],
    viewport: { x: 0, y: 0, k: 1 }, backgroundMode: "dots", showImageInfo: false,
};

async function installApi(page, options = {}) {
    let document = structuredClone(initial);
    if (options.nodes) document.nodes = structuredClone(options.nodes);
    if (options.unbound) delete document.projectId;
    const commits = [];
    const characterCalls = [];
    const normalizes = [];
    let catalogFailure = false;
    let imageFailure = false;
    let characters = [characterFixture()];
    let heldCharacterRead;
    const heldCharacterWaiters = [];
    const viewportWrites = [];
    const preferenceWrites = [];
    const modelWrites = [];
    const assistantCalls = [];
    const assistantMessages = [];
    const assistantConversation = {
        id: "301", project_id: initial.workspaceProjectId, episode_id: null,
        title: "画布讨论", scope_version: 2, stage: null, subject_type: null,
        subject_id: null, task_type: null, row_version: "1", archived: false,
        last_run_status: null, created_at: initial.createdAt, updated_at: initial.updatedAt,
    };
    const legacyConversation = { ...assistantConversation, id: "401", episode_id: "20", title: "历史画布讨论", scope_version: 1 };
    const legacyMessages = [{ id: "1101", seq: 1, role: "assistant", content: "已保存的旧助手消息", references: [], artifacts: [], created_at: initial.createdAt }];
    let modelPreferences = {};
    let holdCommits = Boolean(options.holdCommits);
    const commitWaiters = [];
    let signalHeldCommit;
    const heldCommit = new Promise(resolve => { signalHeldCommit = resolve; });
    const preferences = () => ({ appearance: document.appearance ?? null, backgroundMode: document.backgroundMode, showImageInfo: document.showImageInfo });
    let accessible = true;
    const unimplemented = new Set();
    const summary = () => ({ id: "9007199254740993", source_key: document.id, project_id: document.workspaceProjectId,
        title: document.title, source_project_id: document.projectId || null, row_version: document.revision, schema_version: 1, created_at: document.createdAt, updated_at: document.updatedAt });
    await page.route("**/api/v1/**", async route => {
        const request = route.request();
        const path = new URL(request.url()).pathname.replace("/api/v1", "");
        if (!accessible && (path.includes("/canvases/") || path.includes("/canvas-workspace/resolve/"))) {
            await route.fulfill({ status: 404, json: { error: { code: "not_found", message: "Canvas does not exist" } } });
            return;
        }
        let body;
        if (path === "/auth/me") body = { user: { id: "111", username: "canvas-test", display_name: "画布测试", email: "canvas@example.test" } };
        else if (path === "/agent/models") body = { items: [{ id: "71", name: "对话模型", row_version: "1", model_key: "fixture", protocol: "chat", verified: false, preferred: true }], preferred_id: "71" };
        else if (path === "/agent/skills") body = { items: [], total: 0, offset: 0, limit: 50 };
        else if (path.startsWith("/canvas-runtime/canvas-projects/parity-canvas/character-catalog")) {
            assert.equal(request.headers()["x-canvas-actor"], "111");
            characterCalls.push({ path, method: request.method() });
            const snapshot = { canvas_key: document.id, canvas_row_version: document.revision, source_project_id: document.projectId };
            const item = structuredClone(characters[0]);
            if (heldCharacterRead) await new Promise(resolve => heldCharacterWaiters.push(resolve));
            if (catalogFailure) {
                await route.fulfill({ status: 409, json: { error: { code: "canvas_character_version_conflict", message: "当前角色版本已变化，请刷新" } } }); return;
            }
            if (path.endsWith("image-resources")) {
                if (new URL(request.url()).searchParams.get("expected_version_id") !== item.current_version_id) {
                    await route.fulfill({ status: 409, json: { error: { code: "canvas_character_version_conflict", message: "当前角色版本已变化，请刷新" } } }); return;
                }
                body = { ...snapshot, asset_id: item.asset_id, version_id: item.current_version_id, images: item.representations.map(({ reference_id, role, ordinal, media_id }) => ({ reference_id, role, ordinal, media_id, resource_key: `resource:${media_id}`, file_path: `/api/v1/canvas-runtime/resources/${media_id}/file`, requires_canvas_copy: true, resource: resourceFixture(media_id) })) };
            } else body = path.endsWith("character-catalog") ? { ...snapshot, items: structuredClone(characters) } : { ...snapshot, item };
        }
        else if (path === "/canvas-runtime/resources/normalize") {
            const input = request.postDataJSON(); normalizes.push(input);
            assert.equal(input.canvas_key, "parity-canvas");
            body = { resource_map: Object.fromEntries(input.resource_ids.map(id => [id, id === "9400000000000001" ? "9400000000000002" : id])), resource_aliases: { "9400000000000002": ["9400000000000001"] } };
        }
        else if (/^\/canvas-runtime\/resources\/[0-9]+\/file$/.test(path)) {
            await route.fulfill(imageFailure ? { status: 404, body: "missing image", headers: { "Cache-Control": "no-store" } } : { contentType: "image/png", headers: { "Cache-Control": "no-store" }, body: Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+cRZ0AAAAASUVORK5CYII=", "base64") }); return;
        }
        else if (/^\/canvas-runtime\/resources\/[0-9]+$/.test(path)) body = { resource: resourceFixture(path.split("/").at(-1)) };
        else if (path.startsWith("/canvas-runtime/projects/")) {
            await route.fulfill({ status: 404, json: { error: { code: "not_found", message: "Legacy source project endpoint is unavailable" } } }); return;
        }
        else if (path === "/canvas-runtime/assets" || path === "/canvas-runtime/tasks") body = [];
        else if (path === "/canvas-runtime/plugins/status" && request.method() === "GET") {
            const pluginIds = ["prompt-optimizer", "ai-art-critique", "runninghub-workflow-provider"];
            body = {
                statuses: Object.fromEntries(pluginIds.map(pluginId => [pluginId, "disabled"])),
                states: Object.fromEntries(pluginIds.map(pluginId => [pluginId, {
                    pluginId, platformAvailable: true, userEnabled: false, userConfigured: false,
                    effectiveEnabled: false, canToggle: true, canConfigure: false,
                }])),
            };
        }
        else if (path === `/projects/${initial.workspaceProjectId}/assets`) body = { items: [], total: 0, offset: 0, limit: 20 };
        else if (path === "/agent/conversations/401") body = legacyConversation;
        else if (path === "/agent/conversations/401/messages") body = { items: legacyMessages, total: legacyMessages.length, offset: 0, limit: 50 };
        else if (path.startsWith("/assistant/")) {
            const method = request.method();
            const input = ["POST", "PATCH"].includes(method) ? request.postDataJSON() : null;
            assistantCalls.push({ path, method, body: input, key: request.headers()["idempotency-key"] });
            if (path === "/assistant/conversations/resolve" || path === "/assistant/conversations/301") body = assistantConversation;
            else if (path === "/assistant/conversations") body = { items: [assistantConversation], total: 1, offset: 0, limit: 20 };
            else if (path === "/assistant/legacy-conversations") body = { items: [legacyConversation], total: 1, offset: 0, limit: 20 };
            else if (path.endsWith("/events")) {
                await route.fulfill({ contentType: "text/event-stream", body: ": heartbeat\n\n" });
                return;
            }
            else if (path.endsWith("/state")) body = { conversation_id: "301", cursor: 0, resume_cursor: 0, active_run: null, queued_runs: [] };
            else if (path.endsWith("/runs")) body = { items: [], total: 0, offset: 0, limit: 1 };
            else if (path.endsWith("/attachments")) body = { items: [], total: 0, offset: 0, limit: 50 };
            else if (path.endsWith("/messages") && method === "GET") body = { items: assistantMessages, total: assistantMessages.length, offset: 0, limit: 50 };
            else if (path.endsWith("/messages") && method === "POST") {
                assert.ok(request.headers()["idempotency-key"]);
                assert.equal("task" in input, false);
                assert.equal("mode" in input, false);
                const message = { id: String(1001 + assistantMessages.length), seq: assistantMessages.length + 1, role: "user", content: input.content, references: [], artifacts: [], created_at: initial.createdAt };
                assistantMessages.push(message, { ...message, id: String(1002 + assistantMessages.length), seq: message.seq + 1, role: "assistant", content: "一起分析画面与故事节奏。" });
                body = { message, cursor: 0, run: { id: String(901 + assistantMessages.length), conversation_id: "301", status: "succeeded", phase: "finished", row_version: "1", mode: "discuss", model_config_id: "71", model_name: "对话模型", error: null, usage: {}, budget: {}, review: null, awaiting_artifact_ids: [], created_at: initial.createdAt, updated_at: initial.updatedAt, finished_at: initial.updatedAt, queue_position: 0, waiting_reason: null } };
            } else {
                unimplemented.add(`${method} ${path}`);
                await route.fulfill({ status: 404, json: { error: { code: "not_found", message: "Fixture does not implement this feature" } } });
                return;
            }
        }
        else if (path === "/canvas-runtime/workspace/model-config") {
            if (request.method() === "PUT") {
                const input = request.postDataJSON();
                assert.equal("channels" in input, false);
                modelWrites.push(input);
                if (options.rejectModelPreferences) {
                    await route.fulfill({ status: 409, json: { error: { code: "canvas_model_config_conflict", message: "Model preferences changed" } } });
                    return;
                }
                modelPreferences = input.preferences;
            }
            body = { row_version: String(modelWrites.length), preferences: modelPreferences, models: options.models || [] };
        }
        else if (path === "/canvas-runtime/canvas-projects/parity-canvas/events") {
            await route.fulfill({ status: 200, contentType: "text/event-stream", body: accessible ? ": connected\n\n" : "event: canvas.unavailable\ndata: {}\n\n" });
            return;
        }
        else if (path === "/canvas-workspace") body = { items: [{ ...summary(), canvas_title: document.canvasTitle, node_count: document.nodes.length, preview_nodes: [], source_document: document }], page: 1, page_size: 50, total: 1, has_more: false };
        else if (path === "/canvas-workspace/resolve/parity-canvas") body = summary();
        else if (path.endsWith("/my-document")) body = { ...summary(), source_document: document, resource_aliases: { "9400000000000002": ["9400000000000001"] } };
        else if (path.endsWith("/viewport")) {
            const input = request.postDataJSON();
            assert.deepEqual(input.expected_viewport, document.viewport);
            document.viewport = input.viewport;
            viewportWrites.push(input);
            body = { viewport: document.viewport, preferences: {}, row_version: String(viewportWrites.length) };
        }
        else if (path.endsWith("/view-preferences")) {
            const input = request.postDataJSON();
            assert.deepEqual(input.expected_preferences, preferences());
            document = { ...document, ...input.preferences };
            preferenceWrites.push(input);
            body = { preferences: preferences(), row_version: String(preferenceWrites.length) };
        }
        else if (path.endsWith("/commits")) {
            const input = request.postDataJSON();
            if (holdCommits) await new Promise(resolve => {
                commitWaiters.push(resolve);
                signalHeldCommit();
            });
            if (options.rejectCommits) {
                await route.fulfill({ status: 409, json: { error: { code: "canvas_revision_conflict", message: "画布已变化，当前草稿已保留", details: { current_version: "2" } } } });
                return;
            }
            assert.equal(request.headers()["x-canvas-actor"], "111");
            assert.equal(request.headers()["x-csrf-token"], "browser-fixture");
            assert.ok(request.headers()["idempotency-key"]);
            assert.equal(input.expected_row_version, document.revision);
            document = { ...input.source_document, ...preferences(), viewport: document.viewport, revision: String(BigInt(document.revision) + 1n) };
            commits.push(structuredClone(document));
            body = summary();
        } else {
            unimplemented.add(`${request.method()} ${path}`);
            await route.fulfill({ status: 404, json: { error: { code: "not_found", message: "Fixture does not implement this feature" } } });
            return;
        }
        await route.fulfill({ json: body });
    });
    return { characterCalls, normalizes, failCatalog: value => { catalogFailure = value; }, failImage: value => { imageFailure = value; }, emptyCharacters: () => { characters = []; }, changeCharacter: () => { characters = [characterFixture("9500000000000002")]; }, holdCharacters: () => { heldCharacterRead = true; }, waitingCharacters: () => heldCharacterWaiters.length, releaseCharacters: () => { heldCharacterRead = false; for (const release of heldCharacterWaiters.splice(0)) release(); }, commits, viewportWrites, preferenceWrites, modelWrites, assistantCalls, assistantMessages, unimplemented, document: () => document, revoke: () => { accessible = false; },
        heldCommit, waitingCommits: () => commitWaiters.length, releaseCommits: () => { holdCommits = false; for (const release of commitWaiters.splice(0)) release(); } };
}

function resourceFixture(id) {
    return { id, userId: "111", kind: "image", status: "ready", provider: "minio", endpoint: "", bucket: "", objectKey: "", publicUrl: "", mimeType: "image/png", size: 68, width: 1, height: 1, durationMs: 0, etag: "", createdAt: initial.createdAt, updatedAt: initial.updatedAt };
}
function characterFixture(version = "9500000000000001") {
    return { asset_id: "9200000000000001", asset_row_version: "1", current_version_id: version, version: "2", position: 0, name: "阿青", label: "主角", description: "青衣少女", prompt: "保持青衣", tags: ["主角"], definition: { description: "青衣少女", prompt: "保持青衣" }, definition_origin: "legacy_current", representations: [{ reference_id: "9600000000000001", role: "turnaround_sheet", ordinal: 0, media_id: "9400000000000001", format_code: "png", mime_type: "image/png", original_name: "角色.png", byte_size: 68, width: 1, height: 1 }] };
}
async function until(check, label) {
    const deadline = Date.now() + 15000;
    while (!check()) {
        if (Date.now() > deadline) assert.fail(label);
        await new Promise(resolve => setTimeout(resolve, 40));
    }
}
async function withBrowser(width, options, work) {
    await mkdir(resolve(".runtime/browser/character-current"), { recursive: true });
    const server = await createServer({ configLoader: "runner", cacheDir: testCacheDir, server: { host: "127.0.0.1", port: 0, strictPort: false, fs: { allow: [resolve("."), resolve(".."), realpathSync(resolve("node_modules")), realpathSync(resolve("../node_modules"))] } }, logLevel: "error" });
    let browser;
    try {
        await server.listen();
        const base = `http://127.0.0.1:${server.httpServer.address().port}`;
        browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH });
        const context = await browser.newContext({ viewport: { width, height: 900 }, colorScheme: "dark", reducedMotion: "reduce" });
        try {
            await context.addCookies([{ name: "sd_csrf", value: "browser-fixture", url: base }]);
            const page = await context.newPage(); const errors = []; const external = [];
            page.on("pageerror", error => errors.push(error.message));
            await page.route("**/*", route => {
                if (new URL(route.request().url()).origin !== base) { external.push(route.request().url()); return route.abort(); }
                return route.fallback();
            });
            const api = await installApi(page, options);
            try {
                await page.goto(`${base}/canvas-app/canvas/parity-canvas?libtvChrome=1`, { waitUntil: "domcontentloaded", timeout: 60000 });
                await page.locator('[data-node-id="text-one"]').waitFor({ timeout: 60000 });
                if (width < 1024) await page.getByRole("button", { name: "退出专注模式", exact: true }).click();
                await work({ page, api, base });
            }
            catch (error) {
                await page.screenshot({ path: resolve(`.runtime/browser/character-current/failed-${width}-${Date.now()}.png`) });
                throw new Error(`${error.stack || error}\nfixture calls: ${JSON.stringify(api.characterCalls)}\nfixture commits: ${api.commits.length}\npage errors: ${JSON.stringify(errors)}\nunimplemented: ${JSON.stringify([...api.unimplemented])}\nvisible text: ${await page.locator("body").innerText()}`, { cause: error });
            }
            assert.deepEqual(errors, []);
            assert.deepEqual(external, [], "所有模型、对象与应用网络必须保持本地API替身边界");
            assert.deepEqual([...api.unimplemented], []);
        } finally { await context.close(); }
    } finally { await browser?.close(); await server.close(); }
}
async function openCharacters(page, entry, currentViewport) {
    let expectedPosition;
    if (entry === "studio") await page.getByRole("button", { name: "人像造型室", exact: true }).click();
    else if (entry === "add") {
        await page.getByRole("button", { name: "添加节点", exact: true }).click();
        await page.getByRole("button", { name: "添加角色卡", exact: true }).click();
    } else {
        const viewport = await page.locator("[data-canvas-viewport]").boundingBox();
        if (currentViewport) expectedPosition = { x: (viewport.width - 70 - currentViewport.x) / currentViewport.k, y: (250 - currentViewport.y) / currentViewport.k };
        await page.mouse.click(viewport.x + viewport.width - 70, viewport.y + 250, { button: "right" });
        await page.locator("[data-canvas-context-menu]").getByRole("button", { name: "添加节点", exact: true }).click();
        await page.getByRole("button", { name: "添加角色卡", exact: true }).click();
    }
    await page.locator(".asset-picker-card-action[title='阿青']").waitFor();
    return expectedPosition;
}
async function insertSelected(page) {
    await page.locator(".asset-picker-card-action[title='阿青']").click();
    await page.getByRole("button", { name: "引入已选资产（1）", exact: true }).click();
    await page.locator(".asset-picker-card-action[title='阿青']").waitFor({ state: "hidden" });
}

test("current角色三个原入口、位置尺寸、原详情和复制封面保存/reload（1440/600px，API替身）", { timeout: 240000 }, async () => {
    for (const width of [1440, 600]) await withBrowser(width, {}, async ({ page, api }) => {
        for (const [index, entry] of ["studio", "add", "context"].entries()) {
            const expectedPosition = await openCharacters(page, entry, api.document().viewport); await insertSelected(page);
            await until(() => api.document().nodes.filter(node => node.metadata?.characterAssetId).length === index + 1, `角色${entry}插入必须得到保存回执`);
            const saved = api.document().nodes.filter(node => node.metadata?.characterAssetId).at(-1);
            assert.equal(saved.type, "text"); assert.equal(saved.width, 320); assert.equal(saved.height, 260);
            assert.equal(saved.metadata.characterAssetId, "9200000000000001");
            assert.equal(saved.metadata.characterVersionId, "9500000000000001");
            assert.equal(saved.metadata.characterCoverUrl, "/api/v1/canvas-runtime/resources/9400000000000002/file");
            assert.equal(saved.metadata.characterPrompt, "【角色卡：阿青】\n青衣少女\n保持青衣");
            assert.deepEqual(saved.metadata.characterDefinition, { description: "青衣少女", prompt: "保持青衣" });
            if (entry === "context") {
                assert.ok(Math.abs(saved.position.x + saved.width / 2 - expectedPosition.x) < 1, "context 角色插入须以真实点击的画布x位置为中心");
                assert.ok(Math.abs(saved.position.y + saved.height / 2 - expectedPosition.y) < 1, "context 角色插入须以真实点击的画布y位置为中心");
            }
        }
        const copied = api.document().nodes.filter(node => node.metadata?.characterAssetId).at(-1);
        await page.getByRole("button", { name: "适合屏幕", exact: true }).click();
        await page.locator(`[data-node-id="${copied.id}"]`).dblclick();
        await page.getByText("项目角色引用 · 当前版本", { exact: true }).waitFor();
        await page.getByRole("heading", { name: "阿青", exact: true }).waitFor();
        await page.getByRole("img", { name: "阿青人物三视图", exact: true }).waitFor();
        assert.equal(await page.getByRole("img", { name: "阿青人物三视图", exact: true }).getAttribute("src"), copied.metadata.characterCoverUrl);
        await page.screenshot({ path: resolve(`.runtime/browser/character-current/detail-${width}.png`) });
        await page.locator(".ant-modal-close").click();
        await page.reload();
        await page.locator(`[data-node-id="${copied.id}"]`).waitFor();
        await openCharacters(page, "studio");
        await page.getByRole("button", { name: "取消", exact: true }).click();
        await until(() => api.characterCalls.filter(call => call.path.endsWith("image-resources")).length >= 5, "reload后current resolver必须实际读取");
        assert.equal(api.document().nodes.find(node => node.id === copied.id).metadata.characterCoverUrl, copied.metadata.characterCoverUrl);
        assert.ok(api.normalizes.some(input => input.resource_ids.includes("9400000000000001")));
        assert.ok(api.commits.length >= 3);
    });
});

test("current目录错误/重试、详情图片失败、空与未绑定来源就近反馈（API替身）", { timeout: 180000 }, async () => {
    await withBrowser(1440, {}, async ({ page, api }) => {
        api.failCatalog(true);
        await page.getByRole("button", { name: "人像造型室", exact: true }).click();
        await page.locator(".asset-picker-empty[role='alert']").waitFor();
        assert.match(await page.locator(".asset-picker-empty[role='alert']").innerText(), /修改|变化|版本/);
        api.failCatalog(false); await page.getByRole("button", { name: /重\s*试/ }).click();
        await page.locator(".asset-picker-card-action[title='阿青']").waitFor(); await insertSelected(page);
        await until(() => api.document().nodes.some(node => node.metadata?.characterAssetId), "插入应保存");
        const saved = api.document().nodes.find(node => node.metadata?.characterAssetId);
        api.failImage(true);
        await page.reload();
        await page.locator(`[data-node-id="${saved.id}"]`).waitFor();
        await page.locator(`[data-node-id="${saved.id}"]`).dblclick();
        await page.getByText("角色图片读取失败，请重试", { exact: true }).waitFor();
        api.failImage(false); await page.getByRole("button", { name: /重\s*试/ }).click();
        await page.getByRole("img", { name: "阿青人物三视图", exact: true }).waitFor();
        await page.locator(".ant-modal-close").click();
        api.emptyCharacters();
        await page.getByRole("button", { name: "人像造型室", exact: true }).click();
        await page.getByText("此项目暂无已采用角色。", { exact: true }).waitFor();
        assert.equal(await page.locator(".asset-picker-card-action").count(), 0);
    });
    await withBrowser(600, { unbound: true }, async ({ page, api }) => {
        await page.getByRole("button", { name: "人像造型室", exact: true }).click();
        await page.getByText("此画布尚未关联可读取的项目角色", { exact: true }).waitFor();
        assert.equal(api.characterCalls.length, 0); assert.equal(await page.locator(".asset-picker-card-action").count(), 0);
    });
});

test("current变更刷新和CAS冲突保留草稿，不覆盖服务器（API替身）", { timeout: 150000 }, async () => {
    await withBrowser(1440, {}, async ({ page, api }) => {
        await openCharacters(page, "studio"); await insertSelected(page);
        await until(() => api.document().nodes.some(node => node.metadata?.characterAssetId), "current角色插入应保存");
        api.changeCharacter();
        await page.getByRole("button", { name: "人像造型室", exact: true }).click();
        await page.locator(".asset-picker-card-action[title='阿青'], .asset-picker-empty[role='alert']").waitFor();
        if (await page.locator(".asset-picker-empty[role='alert']").count()) await page.getByRole("button", { name: /重\s*试/ }).click();
        await page.locator(".asset-picker-card-action[title='阿青']").waitFor();
        await page.getByRole("button", { name: "取消", exact: true }).click();
        await until(() => api.document().nodes.some(node => node.metadata?.characterVersionId === "9500000000000002"), "刷新必须保存真实新current版本");
        const updated = api.document().nodes.find(node => node.metadata?.characterAssetId);
        assert.equal(updated.metadata.characterCoverUrl, "/api/v1/canvas-runtime/resources/9400000000000002/file");
    });
    await withBrowser(1440, { rejectCommits: true }, async ({ page, api }) => {
        await openCharacters(page, "studio"); await insertSelected(page);
        await page.getByRole("button", { name: /画布保存状态：.*(冲突|草稿|保存失败)/ }).waitFor();
        assert.equal(api.document().nodes.filter(node => node.metadata?.characterAssetId).length, 0);
        assert.ok(await page.getByText("角色引用", { exact: true }).count() > 0);
        await page.screenshot({ path: resolve(".runtime/browser/character-current/cas-conflict.png") });
    });
});

test("撤权后在途目录响应不回灌、不继续resolver或保存（API替身SSE）", { timeout: 120000 }, async () => {
    await withBrowser(600, {}, async ({ page, api }) => {
        await until(() => api.characterCalls.some(call => call.path.endsWith("image-resources")), "起始current读取必须完成");
        const imageReads = api.characterCalls.filter(call => call.path.endsWith("image-resources")).length;
        api.holdCharacters();
        await page.getByRole("button", { name: "人像造型室", exact: true }).click();
        await until(() => api.waitingCharacters() > 0, "目录读取必须实际在途");
        api.revoke();
        await page.getByText(/画布已被归档或访问权限已变化/).first().waitFor({ timeout: 15000 });
        api.releaseCharacters();
        await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
        assert.equal(api.characterCalls.filter(call => call.path.endsWith("image-resources")).length, imageReads);
        assert.equal(api.commits.length, 0);
        assert.equal(api.document().nodes.filter(node => node.metadata?.characterAssetId).length, 0);
    });
});
