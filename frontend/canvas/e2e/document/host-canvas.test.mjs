import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";
import { resolve } from "node:path";
import test from "node:test";
import { chromium } from "playwright";
import { createServer } from "vite";

const testCacheDir = resolve(".runtime/vite-e2e-host-canvas");

// These fixtures verify the real editor and HTTP adapter, not providers or Python persistence.
const initial = {
    id: "parity-canvas", workspaceProjectId: "9007199254740995", revision: "1",
    title: "迁移对照", canvasTitle: "主画布", createdAt: "2026-01-01T00:00:00Z", updatedAt: "2026-01-01T00:00:00Z",
    nodes: [{ id: "text-one", type: "text", title: "文字节点", position: { x: 420, y: 260 }, width: 320, height: 220, metadata: { content: "画布迁移操作验证" } }],
    connections: [], chatSessions: [], activeChatId: null, directorScenes: [],
    viewport: { x: 0, y: 0, k: 1 }, backgroundMode: "dots", showImageInfo: false,
};

async function installApi(page, options = {}) {
    let document = structuredClone(initial);
    if (options.nodes) document.nodes = structuredClone(options.nodes);
    const commits = [];
    const viewportWrites = [];
    const preferenceWrites = [];
    const modelWrites = [];
    const assistantCalls = [];
    const assistantMessages = [];
    const assistantTasks = [];
    const assistantRuns = [];
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
        title: document.title, row_version: document.revision, schema_version: 1, created_at: document.createdAt, updated_at: document.updatedAt });
    await page.route("**/api/v1/**", async route => {
        const request = route.request();
        const path = new URL(request.url()).pathname.replace("/api/v1", "");
        if (options.route && await options.route(route, path)) return;
        if (!accessible && (path.includes("/canvases/") || path.includes("/canvas-workspace/resolve/"))) {
            await route.fulfill({ status: 404, json: { error: { code: "not_found", message: "Canvas does not exist" } } });
            return;
        }
        let body;
        if (path === "/auth/me") body = { user: { id: "111", username: "canvas-test", display_name: "画布测试", email: "canvas@example.test" } };
        else if (path === "/agent/status") body = { enabled: true, schema_ready: true };
        else if (path === "/agent/models") body = { items: [{ id: "71", name: "对话模型", row_version: "1", model_key: "fixture", protocol: "chat", verified: false, preferred: true }], preferred_id: "71" };
        else if (path === "/agent/skills") body = { items: [], total: 0, offset: 0, limit: 50 };
        else if (path === "/canvas-runtime/assets" || path === "/canvas-runtime/tasks") body = [];
        else if (path === `/projects/${initial.workspaceProjectId}/assets`) body = { items: [], total: 0, offset: 0, limit: 20 };
        else if (path === "/agent/conversations/401") body = legacyConversation;
        else if (path === "/agent/conversations/401/messages") body = { items: legacyMessages, total: legacyMessages.length, offset: 0, limit: 50 };
        else if (path.startsWith("/assistant/")) {
            const method = request.method();
            const input = ["POST", "PATCH"].includes(method) ? request.postDataJSON() : null;
            assistantCalls.push({ path, method, body: input, key: request.headers()["idempotency-key"] });
            if (path === "/assistant/generation-policy") body = { media_generation_enabled: true, policy_version: "1" };
            else if (path === "/assistant/subjects/metadata") body = input.subjects.map(item => ({ ...item, checksum: "a".repeat(64), content_revision: item.revision }));
            else if (/^\/assistant\/tasks\/\d+\/subjects$/.test(path)) {
                const task = assistantTasks.find(item => path.includes(`/tasks/${item.id}/`));
                body = { task_id: task.id, input_revision: task.input_revision, applied_input_revision: task.applied_input_revision, pending: false, items: task.selected_subjects };
            }
            else if (path.startsWith("/assistant/global/")) {
                const conversation = { ...assistantConversation, scope_version: 3, status: "active" };
                if (path === "/assistant/global/conversations") body = { items: [conversation], total: 1, offset: 0, limit: 20 };
                else if (path === "/assistant/global/conversations/301") body = conversation;
                else if (path === "/assistant/global/history") body = { items: [legacyConversation], total: 1, offset: 0, limit: 20 };
                else if (path.endsWith("/state")) body = { conversation_id: "301", snapshot_cursor: 0, resume_cursor: 0, messages: { items: assistantMessages, total: assistantMessages.length, offset: 0, limit: 50 }, tasks: assistantTasks, runs: assistantRuns, operations: [], inputs: [], has_more_tasks: false, has_more_operations: false };
                else if (path.endsWith("/events")) {
                    await route.fulfill({ contentType: "text/event-stream", body: ": heartbeat\n\n" });
                    return;
                }
                else if (path.endsWith("/attachments")) body = { items: [], total: 0, offset: 0, limit: 50 };
                else if (path.endsWith("/messages") && method === "GET") body = { items: assistantMessages, total: assistantMessages.length, offset: 0, limit: 50 };
                else if (path.endsWith("/messages") && method === "POST") {
                    assert.ok(request.headers()["idempotency-key"]);
                    const task = { id: String(501 + assistantTasks.length), conversation_id: "301", goal: input.content, status: "succeeded", phase: "terminal", row_version: "1", plan_revision: "1", input_revision: "1", applied_input_revision: "1", control_seq: "0", applied_control_seq: "0", control_state: "none", execution_target: input.execution_target, memory_project_ids: [initial.workspaceProjectId], source_context: null, selected_subjects: [], skills: [], media_limits: { images: null, videos: null, audio: null }, media_usage: {}, active_operation: null, error: null, created_at: initial.createdAt, updated_at: initial.updatedAt };
                    const message = { id: String(1001 + assistantMessages.length), task_id: task.id, seq: assistantMessages.length + 1, role: "user", content: input.content, references: [], artifacts: [], created_at: initial.createdAt };
                    const run = { id: String(901 + assistantRuns.length), task_id: task.id, conversation_id: "301", status: "succeeded", phase: "finished", row_version: "1", mode: "discuss", model_config_id: "71", model_name: "对话模型", error: null, usage: {}, budget: {}, review: null, awaiting_artifact_ids: [], created_at: initial.createdAt, updated_at: initial.updatedAt, finished_at: initial.updatedAt, queue_position: 0, waiting_reason: null };
                    assistantMessages.push(message, { ...message, id: String(1002 + assistantMessages.length), seq: message.seq + 1, role: "assistant", content: "一起分析画面与故事节奏。" });
                    assistantTasks.push(task); assistantRuns.push(run);
                    body = { message, task, run, cursor: 0 };
                } else {
                    unimplemented.add(`${method} ${path}`);
                    await route.fulfill({ status: 404, json: { error: { code: "not_found", message: "Fixture does not implement this feature" } } });
                    return;
                }
            }
            else if (path === "/assistant/conversations/resolve" || path === "/assistant/conversations/301") body = assistantConversation;
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
            await route.fulfill({ status: 200, contentType: "text/event-stream", body: ": connected\n\n" });
            return;
        }
        else if (path === "/canvas-workspace") body = { items: [{ ...summary(), canvas_title: document.canvasTitle, node_count: document.nodes.length, preview_nodes: [], source_document: document }], page: 1, page_size: 50, total: 1, has_more: false };
        else if (path === "/canvas-workspace/resolve/parity-canvas") body = summary();
        else if (path.endsWith("/my-document")) body = { ...summary(), source_document: document };
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
    return { commits, viewportWrites, preferenceWrites, modelWrites, assistantCalls, assistantMessages, unimplemented, document: () => document, revoke: () => { accessible = false; },
        heldCommit, waitingCommits: () => commitWaiters.length, releaseCommits: () => { holdCommits = false; for (const release of commitWaiters.splice(0)) release(); } };
}

async function waitForSavedDocument(api, predicate) {
    const deadline = Date.now() + 10000;
    while (!predicate(api.document())) {
        assert.ok(Date.now() < deadline, "画布保存回执应包含最新内容");
        await new Promise(resolve => setTimeout(resolve, 50));
    }
}

test("助手来源链接定位远处节点，缺失来源保留画布且不修改作品", { timeout: 180000 }, async () => {
    const baseUrl = "http://127.0.0.1:4182";
    const server = await createServer({ cacheDir: testCacheDir, server: { host: "127.0.0.1", port: 4182, strictPort: true }, logLevel: "error" });
    await server.listen();
    await mkdir(resolve(".runtime/browser"), { recursive: true });
    let browser;
    try {
        browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH });
        for (const width of [1440, 1280]) {
            for (const source of ["far-node", "missing-node", ""]) {
                const context = await browser.newContext({ viewport: { width, height: 900 }, reducedMotion: "reduce" });
                try {
                    await context.addCookies([{ name: "sd_csrf", value: "browser-fixture", url: baseUrl }]);
                    const page = await context.newPage(), errors = [];
                    page.on("pageerror", error => errors.push(error.message));
                    const nodes = [initial.nodes[0], { ...initial.nodes[0], id: "far-node", title: "远处参考", position: { x: 8000, y: 6000 }, metadata: { content: "仅定位，不改正文" } }];
                    const api = await installApi(page, { nodes });
                    await page.goto(`${baseUrl}/canvas-app/canvas/parity-canvas${source ? `?node=${source}` : ""}`);
                    if (source === "missing-node") {
                        const notice = page.getByRole("dialog", { name: "引用来源已移除", exact: true });
                        await notice.getByText("引用来源节点已移除，仍可查看原画布。", { exact: true }).waitFor();
                        await notice.getByRole("button", { name: "返回画布", exact: true }).press("Enter");
                        await notice.waitFor({ state: "hidden" });
                    }
                    await page.getByRole("button", { name: "画布保存状态：已保存", exact: true }).waitFor({ timeout: 60000 });
                    if (source === "far-node") {
                        await page.waitForFunction(() => {
                            const node = document.querySelector('[data-node-id="far-node"][data-node-selected="true"]');
                            if (!node) return false;
                            const bounds = node.getBoundingClientRect();
                            const centerX = bounds.x + bounds.width / 2, centerY = bounds.y + bounds.height / 2;
                            return centerX >= 0 && centerX < innerWidth && centerY >= 0 && centerY < innerHeight;
                        }).catch(async error => {
                            await page.screenshot({ path: resolve(`.runtime/browser/canvas-assistant-source-failure-${width}.png`) });
                            const bounds = await page.locator('[data-node-id="far-node"]').boundingBox();
                            throw new Error(`${error.message}\n${JSON.stringify({ width, source, bounds })}`, { cause: error });
                        });
                        await page.screenshot({ path: resolve(`.runtime/browser/canvas-assistant-source-${width}.png`) });
                    } else {
                        await page.locator('[data-node-id="text-one"]').waitFor();
                        assert.equal(await page.locator('[data-node-selected="true"]').count(), 0);
                    }
                    assert.deepEqual(api.document().nodes, nodes, "来源定位必须保留节点坐标及正文");
                    assert.deepEqual(api.document().connections, []);
                    assert.equal(api.commits.length, 0, "来源定位不得提交业务文档");
                    assert.deepEqual([...api.unimplemented], []);
                    assert.deepEqual(errors, []);
                } finally { await context.close(); }
            }
        }
    } finally { await browser?.close(); await server.close(); }
});

test("视频生成六图参考可提交，准入失败可见且保留已有作品和草稿", { timeout: 180000 }, async () => {
    const baseUrl = "http://127.0.0.1:4182";
    const server = await createServer({ cacheDir: testCacheDir, server: { host: "127.0.0.1", port: 4182, strictPort: true }, logLevel: "error" });
    await server.listen();
    await mkdir(resolve(".runtime/browser"), { recursive: true });
    let browser;
    try {
        browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH });
        for (const [width, rejectsReferences] of [1440, 1280].flatMap(width => [[width, true], [width, false]])) {
            const context = await browser.newContext({ viewport: { width, height: 900 }, reducedMotion: "reduce" });
            await context.addCookies([{ name: "sd_csrf", value: "browser-fixture", url: baseUrl }]);
            const page = await context.newPage();
            const errors = [];
            const submitted = [];
            page.on("pageerror", error => errors.push(error.message));
            const defaults = { version: 1, video: {
                references: { maxImages: 9 }, duration: { selection: "enum", values: Array.from({ length: 12 }, (_, i) => i + 4), default: 5 },
                ratios: ["16:9", "9:16", "1:1", "4:3", "3:4", "21:9"], defaultRatio: "16:9",
                resolutions: ["720p", "480p"], defaultResolution: "720p",
                operations: ["text_to_video", "image_to_video", "reference_to_video"], defaultOperation: "text_to_video",
            } };
            const image = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=", "base64");
            const nodes = Array.from({ length: 6 }, (_, i) => ({ id: `image-${i}`, type: "image", title: `参考图${i + 1}`, position: { x: -1000, y: i * 200 }, width: 160, height: 90,
                metadata: { content: `/api/v1/canvas-runtime/resources/${501 + i}/file`, storageKey: `resource:${501 + i}`, width: 160, height: 90, mimeType: "image/png", status: "success" } }));
            const model = "host-71::seedance-2.0-mini";
            const originalContent = "/api/v1/canvas-runtime/resources/600/file";
            nodes.push({ id: "video-target", type: "video", title: "视频生成测试", position: { x: -140, y: -250 }, width: 280, height: 158,
                metadata: { model, seconds: "15", size: "16:9", vquality: "480p", prompt: "保留六张参考图生成视频", composerContent: "保留六张参考图生成视频",
                    ...(rejectsReferences ? { content: originalContent, storageKey: "resource:600", resultOrigin: "generated", status: "success" } : { nodeRole: "generator", status: "idle", errorDetails: "此前校验失败" }) } });
            const savedModel = { id: "71", name: "已配置视频", model_key: "seedance-2.0-mini", provider: "modelhub", service_type: "video", enabled: true, has_api_key: true, is_default: true,
                runtime_profile: rejectsReferences ? { version: 1, api_format: "openai", protocol: "openai-videos", capability_config: { ...defaults, video: { ...defaults.video, operations: ["text_to_video", "image_to_video"] } } } : null,
                default_capability_config: defaults };
            const taskCreatedAt = new Date().toISOString();
            const api = await installApi(page, { nodes, models: [savedModel], route: async (route, path) => {
                const request = route.request();
                if (path.endsWith("/my-document")) return false;
                if (path === "/canvas-runtime/resources/normalize") await route.fulfill({ json: { resource_map: Object.fromEntries(request.postDataJSON().resource_ids.map(id => [id, id])), resource_aliases: {} } });
                else if (/^\/canvas-runtime\/resources\/\d+\/file$/.test(path)) await route.fulfill({ contentType: "image/png", body: image });
                else if (/^\/canvas-runtime\/resources\/\d+$/.test(path)) await route.fulfill({ json: { id: path.split("/").at(-1), userId: "111", kind: "image", status: "ready", provider: "minio", mimeType: "image/png", size: image.length, width: 160, height: 90 } });
                else if (path === "/canvas-runtime/tasks" && request.method() === "POST") {
                    submitted.push(request.postDataJSON());
                    await route.fulfill({ status: 202, json: { id: "881", projectId: "parity-canvas", type: "canvas_video", status: "queued", operation: "reference_to_video", prompt: "保留六张参考图生成视频", createdAt: taskCreatedAt } });
                } else if (path === "/canvas-runtime/tasks/881") await route.fulfill({ json: { id: "881", projectId: "parity-canvas", type: "canvas_video", status: "running", operation: "reference_to_video", prompt: "保留六张参考图生成视频", createdAt: taskCreatedAt } });
                else return false;
                return true;
            } });
            // 保留真实入边，让生成上下文和参数校验走原编辑器逻辑。
            api.document().connections = nodes.filter(node => node.type === "image").map(node => ({ id: `edge-${node.id}`, fromNodeId: node.id, toNodeId: "video-target" }));
            try {
                await page.goto(`${baseUrl}/canvas-app/canvas/parity-canvas`);
                const video = page.locator('[data-node-id="video-target"]');
                await video.waitFor({ timeout: 60000 });
                // 生成节点可能超出可见画布区域；点击可见区域上缘，避开中心播放按钮。
                const bounds = await video.boundingBox();
                await video.click({ position: { x: Math.min(width - 20, Math.max(20, bounds.x + bounds.width / 2)) - bounds.x, y: Math.max(80, bounds.y + 8) - bounds.y } });
                const composer = page.locator(".canvas-node-composer");
                await composer.waitFor();
                const taskCreated = rejectsReferences ? null : page.waitForResponse(response => response.url().endsWith("/canvas-runtime/tasks") && response.request().method() === "POST");
                await composer.getByRole("button", { name: "生成", exact: true }).click();
                const alert = composer.getByRole("alert");
                if (rejectsReferences) {
                    await alert.waitFor({ timeout: 10000 });
                    assert.match(await alert.innerText(), /不支持全模态参考/);
                    assert.equal(await composer.getByRole("textbox", { name: "视频提示词", exact: true }).innerText(), "保留六张参考图生成视频");
                    await waitForSavedDocument(api, doc => Boolean(doc.nodes.find(node => node.id === "video-target")?.metadata.errorDetails));
                    const rejected = api.document().nodes.find(node => node.id === "video-target");
                    assert.equal(rejected.metadata.content, originalContent);
                    assert.equal(rejected.metadata.status, "success");
                    assert.equal(submitted.length, 0);
                    await page.screenshot({ path: resolve(`.runtime/browser/video-generation-validation-${width}.png`) });
                } else {
                    await page.waitForFunction(() => Boolean(document.querySelector('[data-node-id="video-target"] .animate-spin')), undefined, { timeout: 10000 });
                    await waitForSavedDocument(api, doc => doc.nodes.find(node => node.id === "video-target")?.metadata.status === "loading");
                    await taskCreated;
                    assert.equal(submitted.length, 1);
                    assert.equal(submitted[0].operation, "reference_to_video");
                    assert.equal(submitted[0].logicalModelId, "71");
                    assert.equal(submitted[0].input.referenceImages.length, 6);
                    assert.equal(submitted[0].input.config.videoSeconds, "15");
                    await alert.waitFor({ state: "hidden" });
                    await page.screenshot({ path: resolve(`.runtime/browser/video-generation-loading-${width}.png`) });
                }
                assert.deepEqual(errors, []);
                assert.deepEqual([...api.unimplemented], []);
            } catch (error) {
                await page.screenshot({ path: resolve(`.runtime/browser/video-generation-failure-${width}.png`) }).catch(() => {});
                throw new Error(`${error.message}\n${JSON.stringify({ errors, submitted, unimplemented: [...api.unimplemented], body: await page.locator("body").innerText().catch(() => "") })}`, { cause: error });
            } finally { await context.close(); }
        }
    } finally { await browser?.close(); await server.close(); }
});

test("补齐方案：生成中删除确认、视频右键删除和个人风格冲突另存", { timeout: 120000 }, async () => {
    const baseUrl = "http://127.0.0.1:4182";
    const server = await createServer({ cacheDir: testCacheDir, server: { host: "127.0.0.1", port: 4182, strictPort: true }, logLevel: "error" });
    await server.listen();
    let browser;
    let page;
    const errors = [];
    const changes = [];
    let cancelCalls = 0;
    try {
        browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH });
        const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, reducedMotion: "reduce" });
        await context.addCookies([{ name: "sd_csrf", value: "browser-fixture", url: baseUrl }]);
        page = await context.newPage();
        page.on("pageerror", error => errors.push(error.message));
        const snapshot = { schemaVersion: 1, presetId: "901", sourceProfileId: "901", title: "测试个人风格", description: "冲突测试", tags: [], prompt: "完整画风测试", negativePrompt: "", assets: [], source: "user", revision: 1, executionPolicy: "compatible-fallback" };
        const entity = (id, profile) => ({ id, userId: "111", name: profile.title, description: profile.description, coverUrl: "", tagsJson: "[]", profileJson: JSON.stringify(profile), favorite: false, rowVersion: "1", revision: profile.revision, createdAt: initial.createdAt, updatedAt: initial.updatedAt });
        const profiles = [entity("901", snapshot)];
        const task = { id: "871", projectId: "parity-canvas", type: "canvas_video", operation: "text_to_video", nodeId: "generating-video", status: "running", progress: 20, prompt: "正在生成", provider: "fixture", model: "fixture", createdAt: initial.createdAt, updatedAt: initial.updatedAt };
        const api = await installApi(page, { nodes: [{ ...initial.nodes[0], metadata: { workflowKind: "styleboard" } }, { id: "generating-video", type: "video", title: "正在生成的视频", position: { x: 800, y: 260 }, width: 280, height: 180, metadata: { status: "loading", taskId: "871", taskStatus: "running", prompt: "正在生成" } }], route: async (route, path) => {
            const request = route.request();
            if (path === "/canvas-runtime/tasks/871/cancel") {
                cancelCalls++;
                await route.fulfill({ status: 503, json: { error: { code: "fixture_cancel_failed", message: "测试取消失败" } } });
            } else if (path === "/canvas-runtime/tasks/871") {
                await route.fulfill({ json: task });
            } else if (path === "/canvas-runtime/tasks") {
                await route.fulfill({ json: [task] });
            } else if (path === "/canvas-runtime/style-profiles/preferences") {
                await route.fulfill({ json: { favorites: [], recent: [] } });
            } else if (path === "/canvas-runtime/style-profiles") {
                if (request.method() === "POST") {
                    const input = request.postDataJSON();
                    changes.push({ method: "POST", input });
                    const profile = JSON.parse(input.profileJson);
                    profiles.push(entity("902", { ...profile, presetId: "902", sourceProfileId: "902" }));
                    await route.fulfill({ json: { profile: profiles.at(-1) } });
                } else await route.fulfill({ json: { profiles } });
            } else if (path === "/canvas-runtime/style-profiles/901" && request.method() === "PATCH") {
                const input = request.postDataJSON();
                changes.push({ method: "PATCH", input });
                assert.equal(input.expected_row_version, "1");
                await route.fulfill({ status: 409, json: { error: { code: "canvas_style_conflict", message: "其他会话已修改风格" } } });
            } else return false;
            return true;
        } });
        await page.goto(`${baseUrl}/canvas-app/canvas/parity-canvas?libtvChrome=1`);
        const video = page.locator('[data-node-id="generating-video"]');
        await video.waitFor({ timeout: 60000 });
        await page.getByRole("button", { name: "适合屏幕", exact: true }).click();
        await video.click({ position: { x: 70, y: 8 } });
        await page.keyboard.press("Delete");
        const confirmation = page.getByRole("dialog").filter({ hasText: "删除正在生成的节点？" });
        await confirmation.waitFor();
        await confirmation.getByText("任务已提交后可能产生费用。", { exact: true }).waitFor();
        await confirmation.getByRole("button", { name: "取消", exact: true }).click();
        await confirmation.waitFor({ state: "hidden" });
        assert.equal(cancelCalls, 0);
        assert.equal(await video.count(), 1);
        await video.click({ button: "right", position: { x: 70, y: 8 } });
        await page.getByRole("button", { name: "删除节点", exact: true }).click();
        await confirmation.getByRole("button", { name: "确认删除", exact: true }).click();
        await video.waitFor({ state: "hidden" });
        await page.getByText("节点已删除，未能取消的任务将继续运行。", { exact: true }).waitFor();
        assert.equal(cancelCalls, 1);
        await waitForSavedDocument(api, doc => !doc.nodes.some(node => node.id === "generating-video"));

        await page.getByRole("button", { name: "关闭提示", exact: true }).click();
        await page.getByRole("button", { name: "选择画风", exact: true }).click();
        const center = page.getByRole("dialog").filter({ has: page.getByRole("heading", { name: "风格中心", exact: true }) });
        await center.waitFor();
        await center.getByRole("button", { name: /^我的风格/ }).click();
        await center.getByRole("button", { name: "编辑风格", exact: true }).click();
        const editor = page.getByRole("dialog").filter({ has: page.getByRole("heading", { name: "风格编辑器", exact: true }) });
        const title = editor.getByPlaceholder("例如：东方志怪 · 工笔暗彩");
        await title.fill("保留的冲突草稿");
        await editor.getByRole("button", { name: "保存到我的风格", exact: true }).click();
        await editor.getByRole("alert").waitFor();
        assert.equal(await title.inputValue(), "保留的冲突草稿");
        await editor.getByRole("button", { name: "另存为新风格", exact: true }).click();
        assert.equal(await title.inputValue(), "保留的冲突草稿");
        await editor.getByRole("button", { name: "保存到我的风格", exact: true }).click();
        await editor.waitFor({ state: "hidden" });
        await center.getByRole("heading", { name: "保留的冲突草稿", exact: true }).waitFor();
        assert.deepEqual(changes.map(change => change.method), ["PATCH", "POST"]);
        assert.equal(JSON.parse(changes[1].input.profileJson).title, "保留的冲突草稿");
        assert.deepEqual(errors, []);
    } catch (error) {
        await page?.screenshot({ path: resolve(".runtime/browser/canvas-alignment-delete-style-failure.png") }).catch(() => {});
        throw new Error(`${error.message}\n${JSON.stringify({ errors, cancelCalls, changes, body: await page?.locator("body").innerText().catch(() => "") })}`, { cause: error });
    } finally { await browser?.close(); await server.close(); }
});

test("导演台开发期间隐藏快捷入口，新增菜单和已有节点均不能打开", { timeout: 120000 }, async () => {
    const browserBaseUrl = process.env.CANVAS_BROWSER_BASE_URL || "http://127.0.0.1:4182";
    const server = process.env.CANVAS_BROWSER_BASE_URL ? undefined : await createServer({ cacheDir: testCacheDir, server: { host: "127.0.0.1", port: 4182, strictPort: true }, logLevel: "error" });
    await server?.listen();
    await mkdir(resolve(".runtime/browser"), { recursive: true });
    let browser;
    try {
        browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH });
        for (const size of [{ width: 1440, height: 900 }, { width: 1280, height: 720 }]) {
            const context = await browser.newContext({ viewport: size, reducedMotion: "reduce" });
            try {
                await context.addCookies([{ name: "sd_csrf", value: "browser-fixture", url: browserBaseUrl }]);
                const page = await context.newPage();
                const errors = [];
                page.on("pageerror", error => errors.push(error.message));
                const api = await installApi(page, { nodes: [{
                    id: "director-one", type: "director", title: "导演台 1", position: { x: size.width / 2 - 160, y: 260 }, width: 320, height: 320,
                    metadata: { workflowKind: "shot", directorSceneId: "scene-one", directorShotId: "shot-one" },
                }] });
                await page.goto(`${browserBaseUrl}/canvas-app/canvas/parity-canvas?libtvChrome=1`);
                const node = page.locator('[data-node-id="director-one"]');
                await node.waitFor({ state: "visible", timeout: 60000 }).catch(async error => {
                    await page.screenshot({ path: resolve(`.runtime/browser/director-developing-load-failure-${size.width}.png`) });
                    throw new Error(`${error.message}\n浏览器错误：${JSON.stringify(errors)}\n未实现接口：${JSON.stringify([...api.unimplemented])}\n页面内容：${(await page.locator("body").innerText()).slice(0, 2000)}`, { cause: error });
                });
                await page.getByRole("button", { name: "画布保存状态：已保存", exact: true }).waitFor();
                const exitFocus = page.getByRole("button", { name: "退出专注模式", exact: true });
                if (await exitFocus.isVisible()) await exitFocus.click();
                await page.getByRole("button", { name: "适合屏幕", exact: true }).click();
                const before = structuredClone(api.document());
                assert.equal(await page.locator(".canvas-libtv-director-entry").count(), 0);
                assert.equal(await page.getByRole("button", { name: "打开导演台", exact: true }).count(), 0);
                const nodeEntry = node.getByRole("button", { name: "导演台正在开发", exact: true });
                assert.equal(await nodeEntry.isDisabled(), true);
                await nodeEntry.evaluate(button => button.click());

                const addEntry = page.getByRole("button", { name: "添加节点", exact: true });
                await addEntry.click();
                const menu = page.locator(".canvas-create-menu-surface");
                await menu.waitFor({ state: "visible" });
                const menuEntry = menu.getByRole("button", { name: /^导演台/ });
                assert.equal(await menuEntry.isDisabled(), true);
                assert.match(await menuEntry.textContent(), /正在开发/);
                await menuEntry.evaluate(button => button.click());
                await page.screenshot({ path: resolve(`.runtime/browser/director-developing-menu-${size.width}.png`) });
                await addEntry.click();
                await menu.waitFor({ state: "hidden" });

                const viewport = await page.locator("[data-canvas-viewport]").boundingBox();
                await page.mouse.click(viewport.x + 12, viewport.y + 50, { button: "right" });
                await page.locator("[data-canvas-context-menu]").getByRole("button", { name: "添加节点", exact: true }).click();
                await menu.waitFor({ state: "visible" });
                assert.equal(await menuEntry.isDisabled(), true);
                assert.match(await menuEntry.textContent(), /正在开发/);
                await menuEntry.evaluate(button => button.click());
                assert.equal(await page.locator("[data-director-workbench='true']").count(), 0);
                assert.equal(await page.locator("[data-node-id]").count(), 1);
                assert.deepEqual(api.document().nodes, before.nodes);
                assert.deepEqual(api.document().directorScenes, before.directorScenes);
                assert.deepEqual(errors, []);
                assert.deepEqual([...api.unimplemented], []);
            } finally {
                await context.close();
            }
        }
    } finally {
        await browser?.close();
        await server?.close();
    }
});

test("图形视口预览不重复测量容器，窗口缩放后选区与DPR保持对齐", { timeout: 120000 }, async () => {
    await mkdir(resolve(".runtime/browser"), { recursive: true });
    const browserBaseUrl = process.env.CANVAS_BROWSER_BASE_URL || "http://127.0.0.1:4182";
    const server = process.env.CANVAS_BROWSER_BASE_URL ? undefined : await createServer({ cacheDir: testCacheDir, server: { host: "127.0.0.1", port: 4182, strictPort: true }, logLevel: "error" });
    await server?.listen();
    let browser;
    try {
        browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH });
        for (const deviceScaleFactor of [1, 2]) {
            const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor, colorScheme: "dark", reducedMotion: "no-preference" });
            try {
                await context.addCookies([{ name: "sd_csrf", value: "browser-fixture", url: browserBaseUrl }]);
                const page = await context.newPage();
                const errors = [];
                page.on("pageerror", error => errors.push(error.message));
                const api = await installApi(page, { nodes: [
                    { ...initial.nodes[0], width: 160, height: 180 },
                    { ...initial.nodes[0], id: "text-two", title: "第二文字节点", position: { x: 620, y: 260 }, width: 160, height: 180 },
                ] });
                await page.addInitScript(() => {
                    window.__graphicsPreview = { calls: 0, reads: 0, viewports: [] };
                    let inPreview = false;
                    const dispatch = EventTarget.prototype.dispatchEvent;
                    EventTarget.prototype.dispatchEvent = function (event) {
                        const previous = inPreview;
                        if (event.type === "canvas:graphics-viewport-preview") {
                            inPreview = true;
                            window.__graphicsPreview.calls++;
                            window.__graphicsPreview.viewports.push({ ...event.detail });
                        }
                        try { return dispatch.call(this, event); }
                        finally { inPreview = previous; }
                    };
                    const bounds = Element.prototype.getBoundingClientRect;
                    Element.prototype.getBoundingClientRect = function () {
                        if (inPreview && (this.hasAttribute("data-canvas-viewport") || this.hasAttribute("data-node-id"))) window.__graphicsPreview.reads++;
                        return bounds.call(this);
                    };
                });
                await page.goto(`${browserBaseUrl}/canvas-app/canvas/parity-canvas`);
                const node = page.locator('[data-node-id="text-one"]');
                await node.waitFor({ state: "visible" });
                await page.getByRole("button", { name: "画布保存状态：已保存", exact: true }).waitFor();
                await page.getByRole("button", { name: "适合屏幕", exact: true }).click();
                await page.waitForFunction(() => {
                    const container = document.querySelector("[data-canvas-viewport]");
                    const bounds = document.querySelector('[data-node-id="text-one"]').getBoundingClientRect();
                    return !container.dataset.canvasViewportInteracting && bounds.left >= 0 && bounds.right <= innerWidth && bounds.bottom <= innerHeight - 200;
                });
                await node.locator(".canvas-node-external-header > div > svg").click();
                const panel = page.locator("[data-canvas-node-panel]");
                await panel.waitFor({ state: "visible" });
                const panelBefore = await panel.boundingBox();
                const nodeBefore = await node.boundingBox();
                const before = await page.evaluate(() => ({ ...window.__graphicsPreview }));
                const panBounds = await page.locator("[data-canvas-viewport]").boundingBox();
                const panStart = { x: panBounds.x + panBounds.width - 80, y: panBounds.y + panBounds.height - 140 };
                await page.mouse.move(panStart.x, panStart.y);
                await page.keyboard.down("Space");
                await page.mouse.down();
                for (let step = 1; step <= 12; step++) {
                    await page.mouse.move(panStart.x - step * 3, panStart.y - step * 2);
                    await page.evaluate(() => new Promise(requestAnimationFrame));
                }
                await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
                const panelAfter = await panel.boundingBox();
                const nodeAfter = await node.boundingBox();
                for (const coordinate of ["x", "y"]) {
                    assert.ok(Math.abs((panelAfter[coordinate] - panelBefore[coordinate]) - (nodeAfter[coordinate] - nodeBefore[coordinate])) <= 1, `单选浮层${coordinate}必须逐帧跟随真实节点：${JSON.stringify({ panelBefore, panelAfter, nodeBefore, nodeAfter })}`);
                }
                assert.ok(Math.abs(panelAfter.y - (nodeAfter.y + nodeAfter.height) - 16) <= 1, "无遮挡时单选浮层保持原16px间隔");
                // 在合成预览尚未提交时改变实际容器尺寸。
                await page.setViewportSize({ width: 1280, height: 760 });
                await page.mouse.up();
                await page.keyboard.up("Space");
                const zoomBounds = await page.locator("[data-canvas-viewport]").boundingBox();
                await page.mouse.move(zoomBounds.x + zoomBounds.width - 80, zoomBounds.y + zoomBounds.height - 140);
                await page.keyboard.down("Control");
                for (const delta of [-24, -24, -24, 24, 24, 24]) {
                    await page.mouse.wheel(0, delta);
                    await page.evaluate(() => new Promise(requestAnimationFrame));
                }
                await page.keyboard.up("Control");
                await page.waitForFunction(() => !document.querySelector("[data-canvas-viewport]").dataset.canvasViewportInteracting);
                const after = await page.evaluate(() => ({ ...window.__graphicsPreview }));
                const viewports = after.viewports.slice(before.calls);
                const changed = new Set(viewports.map(viewport => viewport.k)).size > 1 && new Set(viewports.map(viewport => `${viewport.x},${viewport.y}`)).size > 1;
                if (!changed) await page.screenshot({ path: resolve(`.runtime/browser/graphics-preview-dpr${deviceScaleFactor}-failed.png`) });
                assert.ok(changed, `原鼠标平移和缩放必须实际改变图形视口：${JSON.stringify(viewports)}`);
                assert.equal(after.reads - before.reads, 0, "视口预览不能在写入变换后同步测量容器");
                await page.keyboard.down("Control");
                await page.mouse.wheel(0, -48);
                await page.keyboard.up("Control");
                await page.waitForFunction(() => !document.querySelector("[data-canvas-viewport]").dataset.canvasViewportInteracting);
                await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
                const zoomedNode = await node.boundingBox();
                const zoomedPanel = await panel.boundingBox();
                assert.ok(Math.abs(zoomedPanel.y - (zoomedNode.y + zoomedNode.height) - 16) <= 1, `缩放提交后浮层必须继续贴合真实节点边界：${JSON.stringify({ zoomedNode, zoomedPanel })}`);

                for (const size of [{ width: 1280, height: 720 }, { width: 1440, height: 900 }]) {
                    await page.setViewportSize(size);
                    await page.getByRole("button", { name: "适合屏幕", exact: true }).click();
                    await page.waitForFunction(() => {
                        const container = document.querySelector("[data-canvas-viewport]");
                        const node = document.querySelector('[data-node-id="text-one"]');
                        if (!node || container.dataset.canvasViewportInteracting) return false;
                        const bounds = node.getBoundingClientRect();
                        return bounds.left >= 0 && bounds.right <= innerWidth && bounds.top >= 0 && bounds.bottom <= innerHeight;
                    });
                    await page.keyboard.press("Escape");
                    // 标题左端与选中节点的左上缩放手柄重叠；点击节点顶部中央完成选择。
                    await node.click({ position: { x: 80, y: 8 } });
                    await page.locator('[data-node-id="text-two"]').click({ modifiers: ["Shift"], position: { x: 80, y: 8 } });
                    await page.locator("[data-canvas-selection-bounds]").waitFor();
                    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
                    await page.waitForFunction(() => {
                        const container = document.querySelector("[data-canvas-viewport]");
                        const canvas = document.querySelector("[data-canvas-leafer-overlay] canvas");
                        const bounds = container.getBoundingClientRect();
                        return canvas && Math.abs(canvas.width - bounds.width * devicePixelRatio) <= 1 && Math.abs(canvas.height - bounds.height * devicePixelRatio) <= 1;
                    });
                    const geometry = await page.evaluate(() => {
                        const host = document.querySelector("[data-canvas-leafer-overlay]");
                        const canvas = host.querySelector("canvas");
                        const rect = canvas.getBoundingClientRect();
                        const nodes = [...document.querySelectorAll('[data-node-id][data-node-selected="true"]')].map(node => node.getBoundingClientRect());
                        const bounds = { left: Math.min(...nodes.map(node => node.left)), top: Math.min(...nodes.map(node => node.top)), right: Math.max(...nodes.map(node => node.right)), bottom: Math.max(...nodes.map(node => node.bottom)) };
                        const image = canvas.getContext("2d").getImageData(0, 0, canvas.width, canvas.height);
                        let left = Infinity, top = Infinity, right = -Infinity, bottom = -Infinity;
                        for (let y = 0; y < canvas.height; y++) for (let x = 0; x < canvas.width; x++) {
                            if (image.data[(y * canvas.width + x) * 4 + 3] < 20) continue;
                            left = Math.min(left, x); top = Math.min(top, y);
                            right = Math.max(right, x); bottom = Math.max(bottom, y);
                        }
                        return { preview: host.dataset.canvasLeaferPreview || null, painted: Number.isFinite(left), canvas: { left: rect.left, top: rect.top, right: rect.right, bottom: rect.bottom }, selection: { left: rect.left + left / devicePixelRatio, top: rect.top + top / devicePixelRatio, right: rect.left + (right + 1) / devicePixelRatio, bottom: rect.top + (bottom + 1) / devicePixelRatio }, node: { left: bounds.left, top: bounds.top, right: bounds.right, bottom: bounds.bottom } };
                    });
                    assert.equal(geometry.preview, null);
                    assert.ok(geometry.painted, "选区描边必须绘制到真实canvas");
                    for (const [side, padding] of [["left", -12], ["top", -12], ["right", 12], ["bottom", 12]]) {
                        const expected = padding < 0 ? Math.max(geometry.canvas[side], geometry.node[side] + padding) : Math.min(geometry.canvas[side], geometry.node[side] + padding);
                        assert.ok(Math.abs(geometry.selection[side] - expected) <= 2, `${size.width}px/DPR${deviceScaleFactor}选区${side}未对齐：${JSON.stringify(geometry)}`);
                    }
                    await page.screenshot({ path: resolve(`.runtime/browser/graphics-selection-dpr${deviceScaleFactor}-${size.width}.png`) });
                }
                assert.deepEqual(errors, []);
                assert.deepEqual([...api.unimplemented], []);
            } finally {
                await context.close();
            }
        }
    } finally {
        await browser?.close();
        await server?.close();
    }
});

test("原版编辑器拖拽保存、视口和外观独立恢复，撤权后停止编辑", { timeout: 90000 }, async () => {
    // 外部统一 preview 用于复测生产产物；默认仍运行独立画布开发服务器。
    const browserBaseUrl = process.env.CANVAS_BROWSER_BASE_URL || "http://127.0.0.1:4182";
    const server = process.env.CANVAS_BROWSER_BASE_URL ? undefined : await createServer({ cacheDir: testCacheDir, server: { host: "127.0.0.1", port: 4182, strictPort: true }, logLevel: "error" });
    await server?.listen();
    let browser;
    let page;
    let api;
    const diagnostics = { errors: [], unimplemented: [], failedRequests: [], httpErrors: [] };
    await mkdir(resolve(".runtime/browser"), { recursive: true });
    try {
        browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH });
        const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, reducedMotion: "reduce" });
        await context.addCookies([{ name: "sd_csrf", value: "browser-fixture", url: browserBaseUrl }]);
        page = await context.newPage();
        page.on("pageerror", error => diagnostics.errors.push(error.stack ?? error.message));
        page.on("requestfailed", request => diagnostics.failedRequests.push({
            method: request.method(), path: new URL(request.url()).pathname,
            error: request.failure()?.errorText,
        }));
        page.on("response", response => {
            if (response.status() >= 400) diagnostics.httpErrors.push({
                method: response.request().method(), path: new URL(response.url()).pathname,
                status: response.status(),
            });
        });
        api = await installApi(page);
        await page.goto(new URL("/canvas-app/canvas/parity-canvas", browserBaseUrl).href, { waitUntil: "domcontentloaded", timeout: 60000 });
        const node = page.locator('[data-node-id="text-one"]');
        await node.waitFor({ state: "visible", timeout: 60000 });
        await page.locator(".canvas-main-toolbar").waitFor({ state: "visible" });
        await page.evaluate(() => document.fonts.ready);
        const before = await node.boundingBox();
        assert.ok(before);
        const saved = page.waitForResponse(response => response.url().endsWith("/commits") && response.status() === 200);
        await page.mouse.move(before.x + 20, before.y + 8);
        await page.mouse.down();
        await page.mouse.move(before.x + 130, before.y + 68, { steps: 12 });
        await page.mouse.up();
        await page.waitForFunction(() => {
            const node = document.querySelector('[data-node-id="text-one"]');
            return node && node.getBoundingClientRect().x > 480;
        });
        await saved;
        assert.ok(api.commits.some(value => value.nodes[0].position.x !== initial.nodes[0].position.x));
        const after = await node.boundingBox();
        await page.screenshot({ path: resolve(".runtime/browser/canvas-saved.png") });
        await page.reload();
        await node.waitFor({ state: "visible" });
        const restored = await node.boundingBox();
        assert.ok(Math.abs(restored.x - after.x) < 1 && Math.abs(restored.y - after.y) < 1);
        const graphRevision = api.document().revision;
        const viewportSaved = page.waitForResponse(response => response.url().endsWith("/viewport") && response.status() === 200);
        await page.mouse.move(900, 550);
        await page.keyboard.down("Control");
        await page.mouse.wheel(0, -250);
        await page.keyboard.up("Control");
        await viewportSaved;
        assert.equal(api.document().revision, graphRevision);
        assert.ok(api.viewportWrites.length > 0);
        const movedViewport = structuredClone(api.document().viewport);
        await page.reload();
        await node.waitFor({ state: "visible" });
        assert.deepEqual(api.document().viewport, movedViewport);
        const beforeAppearance = api.document().revision;
        await page.getByRole("button", { name: "画布外观", exact: true }).click();
        const lightSaved = page.waitForResponse(response => response.url().endsWith("/view-preferences")
            && response.request().postDataJSON()?.preferences?.appearance?.mode === "light" && response.ok());
        await page.getByRole("button", { name: "切换到浅色主题", exact: true }).click();
        await lightSaved;
        const linesSaved = page.waitForResponse(response => response.url().endsWith("/view-preferences")
            && response.request().postDataJSON()?.preferences?.backgroundMode === "lines" && response.ok());
        await page.getByRole("dialog", { name: "画布外观" }).locator("label.ant-segmented-item").filter({ hasText: /^线$/ }).click();
        await linesSaved;
        assert.equal(api.document().revision, beforeAppearance);
        await page.reload();
        await node.waitFor({ state: "visible" });
        await page.getByRole("button", { name: "画布外观", exact: true }).click();
        assert.equal(await page.getByRole("button", { name: "切换到浅色主题", exact: true }).getAttribute("aria-pressed"), "true");
        assert.equal(await page.getByRole("dialog", { name: "画布外观" }).getByRole("radio", { name: "线", exact: true }).isChecked(), true);
        await page.keyboard.press("Escape");
        const commitsBeforeRevoke = api.commits.length;
        api.revoke();
        await page.getByRole("alert").filter({ hasText: "访问权限已变化" }).waitFor({ state: "visible", timeout: 15000 });
        assert.equal(await node.isVisible(), false);
        assert.equal(api.commits.length, commitsBeforeRevoke);
        await page.route("**/projects", route => route.fulfill({ contentType: "text/html; charset=utf-8", body: "<main>宿主项目列表</main>" }));
        await page.getByRole("button", { name: "返回工作台", exact: true }).click();
        await page.waitForURL(new URL("/projects", browserBaseUrl).href);
        await page.getByText("宿主项目列表").waitFor({ state: "visible" });
        diagnostics.unimplemented = [...api.unimplemented];
        assert.deepEqual(diagnostics.errors, []);
        assert.deepEqual(diagnostics.unimplemented, []);
    } catch (error) {
        diagnostics.unimplemented = [...(api?.unimplemented ?? [])];
        if (page) {
            await page.screenshot({ path: resolve(".runtime/browser/canvas-failure.png") }).catch(() => {});
            diagnostics.body = await page.locator("body").innerText().catch(() => "");
        }
        throw new Error(`${error.message}\n${JSON.stringify(diagnostics)}`, { cause: error });
    } finally {
        await browser?.close();
        await server?.close();
    }
});

test("助手首次加载中关闭后再次打开可恢复对话", { timeout: 120000 }, async () => {
    const baseUrl = process.env.CANVAS_BROWSER_BASE_URL || "http://127.0.0.1:4182";
    const server = process.env.CANVAS_BROWSER_BASE_URL ? undefined : await createServer({ cacheDir: testCacheDir, server: { host: "127.0.0.1", port: 4182, strictPort: true }, logLevel: "error" });
    await server?.listen();
    let browser;
    try {
        browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH });
        for (const width of [1440, 1280]) {
            const context = await browser.newContext({ viewport: { width, height: 900 }, reducedMotion: "reduce" });
            await context.addCookies([{ name: "sd_csrf", value: "browser-fixture", url: baseUrl }]);
            const page = await context.newPage();
            const errors = [];
            page.on("pageerror", error => errors.push(error.stack ?? error.message));
            const api = await installApi(page);
            let holdInitial = true;
            let resolveRequests = 0;
            let releaseInitial;
            const pendingInitial = new Promise(resolve => { releaseInitial = resolve; });
            await page.route(/\/api\/v1\/assistant\/global\/conversations(?:\?|$)/, async route => {
                resolveRequests += 1;
                if (holdInitial) await pendingInitial;
                await route.fallback();
            });
            try {
                await page.goto(new URL("/canvas-app/canvas/parity-canvas", baseUrl).href, { waitUntil: "domcontentloaded", timeout: 60000 });
                const initialStarted = page.waitForRequest(/\/api\/v1\/assistant\/global\/conversations(?:\?|$)/, { timeout: 15000 });
                await page.getByRole("button", { name: "AI 创作助手", exact: true }).click();
                await initialStarted;
                await page.getByRole("button", { name: "关闭助手", exact: true }).click();
                const input = page.getByRole("textbox", { name: "给助手的消息" });
                await input.waitFor({ state: "hidden" });
                const beforeReopen = resolveRequests;
                holdInitial = false;
                await page.getByRole("button", { name: "AI 创作助手", exact: true }).click();
                await input.waitFor({ state: "visible", timeout: 10000 });
                assert.ok(resolveRequests > beforeReopen);
                await input.fill("重新打开后仍可编辑");
                releaseInitial();
                assert.equal(await input.inputValue(), "重新打开后仍可编辑");
                assert.deepEqual([...api.unimplemented], []);
                assert.deepEqual(errors, []);
            } finally {
                releaseInitial();
                await context.close();
            }
        }
    } finally {
        await browser?.close();
        await server?.close();
    }
});

test("设置与工作台导航等待画布保存，409保留草稿", { timeout: 180000 }, async () => {
    const baseUrl = process.env.CANVAS_BROWSER_BASE_URL || "http://127.0.0.1:4182";
    const server = process.env.CANVAS_BROWSER_BASE_URL ? undefined : await createServer({ cacheDir: testCacheDir, server: { host: "127.0.0.1", port: 4182, strictPort: true }, logLevel: "error" });
    await server?.listen();
    let browser;
    let privatePreferenceServer;
    try {
        browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH });
        const direct = await browser.newPage();
        await installApi(direct);
        await direct.route("**/ai_config*", route => route.fulfill({ contentType: "text/html; charset=utf-8", body: "<main>宿主模型配置</main>" }));
        for (const [suffix, expected] of [["?section=channels", "/ai_config"], ["?return_to=https%3A%2F%2Fevil.test", "/ai_config"], ["?return_to=%2Fcanvas-app%2Fcanvas%2Fparity-canvas", "/ai_config?return_to=%2Fcanvas-app%2Fcanvas%2Fparity-canvas"]]) {
            await direct.goto(new URL(`/canvas-app/settings${suffix}`, baseUrl).href, { waitUntil: "domcontentloaded", timeout: 60000 });
            await direct.waitForURL(new URL(expected, baseUrl).href, { timeout: 60000 });
            await direct.getByText("宿主模型配置").waitFor({ state: "visible" });
        }
        await direct.close();

        for (const [destination, rejectCommits, editDuringSave] of [["模型配置", false], ["模型配置", true], ["返回工作台", false], ["返回工作台", true], ["返回工作台", false, true]]) {
            const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, reducedMotion: "reduce" });
            await context.addCookies([{ name: "sd_csrf", value: "browser-fixture", url: baseUrl }]);
            const page = await context.newPage();
            const errors = [];
            page.on("pageerror", error => errors.push(error.stack ?? error.message));
            const api = await installApi(page, { holdCommits: true, rejectCommits });
            await page.route("**/ai_config*", route => route.fulfill({ contentType: "text/html; charset=utf-8", body: "<main>宿主模型配置</main>" }));
            await page.route("**/projects", route => route.fulfill({ contentType: "text/html; charset=utf-8", body: "<main>宿主项目列表</main>" }));
            await page.goto(new URL("/canvas-app/canvas/parity-canvas", baseUrl).href, { waitUntil: "domcontentloaded", timeout: 60000 });
            const node = page.locator('[data-node-id="text-one"]');
            await node.waitFor({ state: "visible", timeout: 60000 });
            const before = await node.boundingBox();
            await page.mouse.move(before.x + 20, before.y + 8);
            await page.mouse.down();
            await page.mouse.move(before.x + 130, before.y + 68, { steps: 12 });
            await page.mouse.up();
            for (let attempt = 0; api.waitingCommits() === 0 && attempt < 100; attempt++) await new Promise(resolve => setTimeout(resolve, 50));
            assert.equal(api.waitingCommits(), 1);
            await page.getByRole("button", { name: "打开画布菜单", exact: true }).click();
            await page.getByRole("menuitem", { name: destination, exact: true }).press("Enter");
            assert.equal(new URL(page.url()).pathname, "/canvas-app/canvas/parity-canvas");
            await page.getByRole("button", { name: "画布保存状态：正在保存", exact: true }).waitFor({ state: "visible" });
            if (editDuringSave) {
                await page.getByRole("button", { name: "编辑节点名称：文字节点", exact: true }).click();
                const title = page.getByRole("textbox", { name: "节点名称", exact: true });
                await title.fill("保存期间的新节点名称");
                await title.press("Enter");
                await page.getByRole("button", { name: "编辑节点名称：保存期间的新节点名称", exact: true }).waitFor({ state: "visible" });
            }
            api.releaseCommits();
            if (editDuringSave) {
                await page.getByRole("alert").filter({ hasText: "保存期间内容再次变化" }).waitFor({ state: "visible" }).catch(async error => {
                    throw new Error(`${error.message}\n${JSON.stringify({ url: page.url(), commits: api.commits, body: await page.locator("body").innerText() })}`, { cause: error });
                });
                assert.equal(new URL(page.url()).pathname, "/canvas-app/canvas/parity-canvas");
                assert.equal(await page.getByRole("button", { name: "编辑节点名称：保存期间的新节点名称", exact: true }).isVisible(), true);
            } else if (rejectCommits) {
                await page.getByRole("button", { name: "画布保存状态：版本冲突 · 未同步", exact: true }).waitFor({ state: "visible" });
                await page.getByRole("alert").waitFor({ state: "visible" });
                await page.getByText("此画布的自动提交已暂停。加载最新版前会保留本地草稿，可下载后从画布列表导入为副本。", { exact: true }).waitFor({ state: "visible" });
                assert.equal(new URL(page.url()).pathname, "/canvas-app/canvas/parity-canvas");
                const draft = await node.boundingBox();
                assert.ok(draft.x > before.x + 80);
                assert.equal(api.commits.length, 0);
            } else {
                await page.waitForURL(new URL(destination === "模型配置" ? "/ai_config?return_to=%2Fcanvas-app%2Fcanvas%2Fparity-canvas" : "/projects", baseUrl).href);
                assert.ok(api.commits.length > 0);
                assert.ok(api.document().nodes[0].position.x > initial.nodes[0].position.x);
                await page.getByText(destination === "模型配置" ? "宿主模型配置" : "宿主项目列表").waitFor({ state: "visible" });
            }
            assert.deepEqual(errors, []);
            await context.close();
        }

        // This section injects an existing private preference through the real development store.
        // The API remains a fixture; this does not verify a user model-selection UI or providers.
        privatePreferenceServer = await createServer({ cacheDir: resolve(".runtime/vite-e2e-private-preferences"), server: { host: "127.0.0.1", port: 4183, strictPort: true }, logLevel: "error" });
        await privatePreferenceServer.listen();
        const preferencesBaseUrl = "http://127.0.0.1:4183";
        const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, reducedMotion: "reduce" });
        await context.addCookies([{ name: "sd_csrf", value: "browser-fixture", url: preferencesBaseUrl }]);
        const page = await context.newPage();
        const models = [
            { id: "91", name: "第一文本模型", model_key: "first", provider: "fixture", service_type: "text", enabled: true, has_api_key: true, is_default: true },
            { id: "92", name: "第二文本模型", model_key: "second", provider: "fixture", service_type: "text", enabled: true, has_api_key: true },
        ];
        const api = await installApi(page, { models, rejectModelPreferences: true });
        await page.goto(new URL("/canvas-app/canvas/parity-canvas", preferencesBaseUrl).href, { waitUntil: "domcontentloaded", timeout: 60000 });
        await page.locator('[data-node-id="text-one"]').waitFor({ state: "visible", timeout: 60000 });
        const preferencesFailed = page.waitForResponse(response => response.url().endsWith("/workspace/model-config") && response.status() === 409);
        await page.evaluate(async () => {
            const { useConfigStore } = await import("/canvas-app/src/stores/use-config-store.ts");
            useConfigStore.getState().updateConfig("textModel", "host-92::second");
        });
        await preferencesFailed;
        await page.getByRole("button", { name: "打开画布菜单", exact: true }).click();
        await page.getByRole("menuitem", { name: "模型配置", exact: true }).click();
        await page.getByRole("button", { name: "画布保存状态：离开画布未完成", exact: true }).waitFor({ state: "visible" });
        await page.getByRole("alert").filter({ hasText: "数据已被修改，请重新加载最新内容后再操作。" }).waitFor({ state: "visible" });
        assert.equal(new URL(page.url()).pathname, "/canvas-app/canvas/parity-canvas");
        assert.equal(api.modelWrites.length, 1);
        assert.equal(api.modelWrites[0].preferences.textModel, "host-92::second");
        assert.equal(api.commits.length, 0);
        assert.equal(await page.evaluate(async () => {
            const { useConfigStore } = await import("/canvas-app/src/stores/use-config-store.ts");
            return useConfigStore.getState().config.textModel;
        }), "host-92::second");
        await context.close();
    } finally {
        await browser?.close();
        await server?.close();
        await privatePreferenceServer?.close();
    }
});


test("共用创作助手保存后引用画布、保留草稿并只读打开旧记录", { timeout: 120000 }, async () => {
    const baseUrl = process.env.CANVAS_BROWSER_BASE_URL || "http://127.0.0.1:4182";
    const server = process.env.CANVAS_BROWSER_BASE_URL ? undefined : await createServer({ cacheDir: testCacheDir, server: { host: "127.0.0.1", port: 4182, strictPort: true }, logLevel: "error" });
    await server?.listen();
    let browser;
    let page;
    const errors = [];
    const legacyRequests = [];
    await mkdir(resolve(".runtime/browser"), { recursive: true });
    try {
        browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH });
        const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, reducedMotion: "reduce" });
        await context.addCookies([{ name: "sd_csrf", value: "browser-fixture", url: baseUrl }]);
        page = await context.newPage();
        page.on("pageerror", error => errors.push(error.stack ?? error.message));
        page.on("request", request => {
            if (request.url().includes("/agent/conversations/")) legacyRequests.push(request.url());
        });
        const api = await installApi(page, { holdCommits: true });
        await page.goto(new URL("/canvas-app/canvas/parity-canvas", baseUrl).href, { waitUntil: "domcontentloaded", timeout: 60000 });
        const node = page.locator('[data-node-id="text-one"]');
        await node.waitFor({ state: "visible", timeout: 60000 });
        await page.evaluate(() => document.fonts.ready);
        await page.getByRole("button", { name: "AI 创作助手", exact: true }).click();
        const input = page.getByRole("textbox", { name: "给助手的消息" });
        await input.waitFor({ state: "visible" });
        await input.fill("@文字");
        await page.getByRole("menuitemcheckbox", { name: "文字节点 节点", exact: true }).click();
        await page.getByRole("button", { name: "移除素材文字节点", exact: true }).waitFor({ state: "visible" });
        const before = await node.boundingBox();
        assert.ok(before);
        const startedCommit = page.waitForRequest(request => request.url().endsWith("/commits"));
        await page.mouse.move(before.x + 20, before.y + 8);
        await page.mouse.down();
        await page.mouse.move(before.x + 130, before.y + 68, { steps: 12 });
        await page.mouse.up();
        await startedCommit;
        await api.heldCommit;
        assert.equal(api.waitingCommits(), 1);
        assert.equal(await page.locator(".ai-assistant-composer .ant-sender textarea").count(), 1);
        await input.fill("一起分析这个文字节点");
        await page.getByRole("button", { name: "发送", exact: true }).click();
        await page.locator(".ai-assistant-send.ant-btn-loading").waitFor({ state: "visible" });
        assert.equal(api.assistantCalls.filter(call => call.method === "POST" && call.path.endsWith("/messages")).length, 0);
        const received = page.waitForResponse(response => response.url().endsWith("/assistant/global/conversations/301/messages") && response.request().method() === "POST");
        api.releaseCommits();
        await received;
        await page.waitForFunction(() => document.querySelector('.ai-assistant-mention-input textarea')?.value === "");
        let sends = api.assistantCalls.filter(call => call.method === "POST" && call.path.endsWith("/messages"));
        assert.equal(sends.length, 1);
        assert.deepEqual(sends[0].body.execution_target, { mode: "infinite_canvas", project_id: initial.workspaceProjectId, canvas_id: "parity-canvas" });
        assert.equal(sends[0].body.new_task, true);
        assert.equal(api.document().revision, "2");
        await input.fill("@文字");
        await page.getByRole("menuitemcheckbox", { name: "文字节点 节点", exact: true }).click();
        await page.getByRole("button", { name: "移除素材文字节点", exact: true }).waitFor({ state: "visible" });
        await input.fill("仅讨论手工引用的节点");
        const selectedReceived = page.waitForResponse(response => response.url().endsWith("/assistant/global/conversations/301/messages") && response.request().method() === "POST");
        await page.getByRole("button", { name: "发送", exact: true }).click();
        await selectedReceived;
        await page.waitForFunction(() => document.querySelector('.ai-assistant-mention-input textarea')?.value === "");
        sends = api.assistantCalls.filter(call => call.method === "POST" && call.path.endsWith("/messages"));
        assert.equal(sends.length, 2);
        assert.deepEqual(sends[1].body.subject_selection.subjects, [{ project_id: initial.workspaceProjectId, source_kind: "canvas", source_id: "parity-canvas", kind: "node", id: "text-one", revision: api.document().revision, content_revision: api.document().revision, checksum: "a".repeat(64) }]);
        assert.notEqual(sends[0].key, sends[1].key);
        await input.fill("保留的画布对话草稿");
        await page.getByRole("button", { name: "关闭助手", exact: true }).click();
        await input.waitFor({ state: "hidden" });
        await page.getByRole("button", { name: "AI 创作助手", exact: true }).click();
        await input.waitFor({ state: "visible" });
        assert.equal(await input.inputValue(), "保留的画布对话草稿");
        const docked = await page.locator("aside.creative-assistant-canvas").boundingBox();
        assert.ok(docked && docked.width === 380 && docked.x + docked.width <= 1440);
        await page.locator(".ai-assistant-bubbles .ant-bubble").first().waitFor({ state: "visible" });
        assert.equal(await page.evaluate(() => document.documentElement.classList.contains("dark")), true);
        const darkPanelBackground = await page.locator(".ai-assistant-panel").evaluate(element => getComputedStyle(element).backgroundColor);
        assert.notEqual(darkPanelBackground, "rgba(0, 0, 0, 0)");
        await page.mouse.move(500, 100);
        await page.getByRole("tooltip", { name: "新对话", exact: true }).waitFor({ state: "hidden" });
        await page.screenshot({ path: resolve(".runtime/browser/shared-canvas-assistant-1440.png") });
        await page.getByRole("button", { name: "画布外观", exact: true }).click();
        const lightSaved = page.waitForResponse(response => response.url().endsWith("/view-preferences")
            && response.request().postDataJSON()?.preferences?.appearance?.mode === "light" && response.ok());
        await page.getByRole("button", { name: "切换到浅色主题", exact: true }).click();
        await lightSaved;
        await page.getByRole("button", { name: "画布外观", exact: true }).click();
        await page.waitForFunction(() => !document.documentElement.classList.contains("dark"));
        const lightPanelBackground = await page.locator(".ai-assistant-panel").evaluate(element => getComputedStyle(element).backgroundColor);
        assert.notEqual(lightPanelBackground, "rgba(0, 0, 0, 0)");
        assert.notEqual(lightPanelBackground, darkPanelBackground);
        assert.equal(await input.inputValue(), "保留的画布对话草稿");
        await page.screenshot({ path: resolve(".runtime/browser/shared-canvas-assistant-1440-light.png") });
        await page.setViewportSize({ width: 1280, height: 900 });
        await input.waitFor({ state: "visible" });
        assert.equal(await input.inputValue(), "保留的画布对话草稿");
        await page.waitForFunction(() => {
            const panel = document.querySelector(".canvas-assistant-sidebar .ai-assistant-panel");
            const bounds = panel?.getBoundingClientRect();
            return bounds && bounds.x >= 0 && bounds.right <= window.innerWidth + 1;
        });
        const desktop = await page.locator(".canvas-assistant-sidebar .ai-assistant-panel").boundingBox();
        assert.ok(desktop && desktop.x >= 0 && desktop.x + desktop.width <= 1281);
        await page.locator(".canvas-assistant-sidebar .ai-assistant-bubbles .ant-bubble").first().waitFor({ state: "visible" });
        await page.waitForFunction(() => document.querySelector('.canvas-assistant-sidebar .ai-assistant-send')?.disabled === false);
        await page.screenshot({ path: resolve(".runtime/browser/shared-canvas-assistant-1280.png") });
        await page.goto(new URL("/canvas-app/canvas/parity-canvas?agent=1&conversation=401", baseUrl).href, { waitUntil: "domcontentloaded", timeout: 60000 });
        const legacy = page.getByRole("dialog", { name: "历史画布讨论", exact: true });
        await legacy.waitFor({ state: "visible" });
        await legacy.getByText("已保存的旧助手消息", { exact: true }).waitFor({ state: "visible" });
        assert.equal(await legacy.getByRole("button", { name: "发送", exact: true }).count(), 0);
        const params = new URL(page.url()).searchParams;
        assert.equal(params.get("assistant"), "1");
        assert.equal(params.get("legacy_conversation"), "401");
        assert.equal(params.has("agent"), false);
        assert.notEqual(params.get("conversation"), "401");
        assert.equal(api.assistantCalls.filter(call => call.method === "POST" && call.path.endsWith("/messages")).length, 2);
        assert.deepEqual([...api.unimplemented], []);
        assert.deepEqual(errors, []);
        await context.close();
    } catch (error) {
        if (page) await page.screenshot({ path: resolve(".runtime/browser/shared-canvas-assistant-failure.png") }).catch(() => {});
        throw new Error(`${error.message}\n${JSON.stringify({ errors, url: page?.url(), legacyRequests, body: await page?.locator("body").innerText().catch(() => "") })}`, { cause: error });
    } finally {
        await browser?.close();
        await server?.close();
    }
});

test("补齐方案：上传确认、音视频字幕、兼容预览、历史刷新及底部快捷键", { timeout: 180000 }, async () => {
    const baseUrl = "http://127.0.0.1:4182";
    const server = await createServer({ cacheDir: testCacheDir, server: { host: "127.0.0.1", port: 4182, strictPort: true }, logLevel: "error" });
    await server.listen();
    // 0.5 秒蓝色视频和正弦波音轨；只验证浏览器解码及字幕交互，不验证语音模型。
    const preview = Buffer.from("AAAAIGZ0eXBpc29tAAACAGlzb21pc28yYXZjMW1wNDEAAAAIZnJlZQAAFYJtZGF0AAACrgYF//+q3EXpvebZSLeWLNgg2SPu73gyNjQgLSBjb3JlIDE2NSByMzIyMyAwNDgwY2IwIC0gSC4yNjQvTVBFRy00IEFWQyBjb2RlYyAtIENvcHlsZWZ0IDIwMDMtMjAyNSAtIGh0dHA6Ly93d3cudmlkZW9sYW4ub3JnL3gyNjQuaHRtbCAtIG9wdGlvbnM6IGNhYmFjPTEgcmVmPTMgZGVibG9jaz0xOjA6MCBhbmFseXNlPTB4MzoweDExMyBtZT1oZXggc3VibWU9NyBwc3k9MSBwc3lfcmQ9MS4wMDowLjAwIG1peGVkX3JlZj0xIG1lX3JhbmdlPTE2IGNocm9tYV9tZT0xIHRyZWxsaXM9MSA4eDhkY3Q9MSBjcW09MCBkZWFkem9uZT0yMSwxMSBmYXN0X3Bza2lwPTEgY2hyb21hX3FwX29mZnNldD0tMiB0aHJlYWRzPTEgbG9va2FoZWFkX3RocmVhZHM9MSBzbGljZWRfdGhyZWFkcz0wIG5yPTAgZGVjaW1hdGU9MSBpbnRlcmxhY2VkPTAgYmx1cmF5X2NvbXBhdD0wIGNvbnN0cmFpbmVkX2ludHJhPTAgYmZyYW1lcz0zIGJfcHlyYW1pZD0yIGJfYWRhcHQ9MSBiX2JpYXM9MCBkaXJlY3Q9MSB3ZWlnaHRiPTEgb3Blbl9nb3A9MCB3ZWlnaHRwPTIga2V5aW50PTI1MCBrZXlpbnRfbWluPTEwIHNjZW5lY3V0PTQwIGludHJhX3JlZnJlc2g9MCByY19sb29rYWhlYWQ9NDAgcmM9Y3JmIG1idHJlZT0xIGNyZj0yMy4wIHFjb21wPTAuNjAgcXBtaW49MCBxcG1heD02OSBxcHN0ZXA9NCBpcF9yYXRpbz0xLjQwIGFxPTE6MS4wMACAAAAAGmWIhAAQ//7mwPmWWiHjgFCHa4ZOtItjn3d9AAAACEGaJGxDf/6r3gIATGF2YzYyLjExLjEwMAACYKxbqVByJqV49eP3rriW1UlqlpUkyTuDoQEGFXLxdxrpLWvaXmPBu2PO6vk8UtgzPkvnnTXSPN3Nu4tg5S4jr7VNtWzZVO1Vn3VWI4VjcVcbNoNmxtixNystyznXsTjsbirjZrjP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", "base64");
    let browser;
    try {
        browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH });
        for (const viewport of [{ width: 1440, height: 900 }, { width: 1280, height: 720 }]) {
            const context = await browser.newContext({ viewport, reducedMotion: "reduce" });
            try {
                await context.addCookies([{ name: "sd_csrf", value: "browser-fixture", url: baseUrl }]);
                const page = await context.newPage();
                const errors = [];
                page.on("pageerror", error => errors.push(error.message));
                let prepared = false;
                let prepareCalls = 0;
                let historyReads = 0;
                let detailReads = 0;
                let transcriptions = 0;
                let holdHistoryAsset = false;
                let historyAssetAttempts = 0;
                let releaseHistoryAsset;
                let signalHistoryAsset;
                const historyAssetRequest = new Promise(resolve => { signalHistoryAsset = resolve; });
                const historyAssetResponse = new Promise(resolve => { releaseHistoryAsset = resolve; });
                let holdHistoryCommit = false;
                let releaseHistoryCommit;
                let signalHistoryCommit;
                const historyCommitRequest = new Promise(resolve => { signalHistoryCommit = resolve; });
                const historyCommitResponse = new Promise(resolve => { releaseHistoryCommit = resolve; });
                const savedAssets = new Map();
                const resourceCalls = [];
                const urlImportKeys = [];
                const task = { id: "811", projectId: "parity-canvas", type: "canvas_video", operation: "text_to_video",
                    status: "succeeded", progress: 100, prompt: "历史测试视频", provider: "fixture", model: "fixture",
                    previewKind: "video", previewUrl: "/api/v1/canvas-runtime/resources/501/file",
                    createdAt: initial.createdAt, updatedAt: initial.updatedAt, resultState: "READY",
                    resultJson: JSON.stringify({ video: { storageKey: "resource:501", url: "/api/v1/canvas-runtime/resources/501/file", durationMs: 500, width: 160, height: 90 } }) };
                const resource = id => ({ id, userId: "111", kind: id === "502" ? "audio" : "video", status: "ready", provider: "minio",
                    endpoint: "", bucket: "", objectKey: "", publicUrl: `/api/v1/canvas-runtime/resources/${id}/file`,
                    mimeType: "video/mp4", size: preview.length, width: 160, height: 90, durationMs: 500,
                    createdAt: initial.createdAt, updatedAt: initial.updatedAt, ...(prepared ? { playbackStatus: "ready", playbackTaskId: "821" } : {}) });
                const nodes = [
                    { id: "video-one", type: "video", title: "测试视频", position: { x: 320, y: 240 }, width: 280, height: 180, metadata: { content: "/api/v1/canvas-runtime/resources/501/file", storageKey: "resource:501", durationMs: 500, status: "success" } },
                    { id: "audio-one", type: "audio", title: "测试音频", position: { x: 650, y: 240 }, width: 280, height: 180, metadata: { content: "/api/v1/canvas-runtime/resources/502/file", storageKey: "resource:502", durationMs: 500, status: "success" } },
                ];
                const api = await installApi(page, { nodes, route: async (route, path) => {
                    const request = route.request();
                    if (path.includes("/resources/")) resourceCalls.push(request.method() + " " + request.url());
                    if (path === "/canvas-runtime/resources/import") {
                        urlImportKeys.push(request.headers()["x-idempotency-key"]);
                        await route.fulfill({ status: 503, json: { error: { code: "fixture_import_failed", message: "测试链接暂不可读取" } } });
                    } else if (path === "/canvas-runtime/resources" && request.method() === "POST") {
                        assert.ok(request.headers()["x-idempotency-key"]);
                        await route.fulfill({ json: { resource: { ...resource("503"), kind: "file", mimeType: "text/plain", size: Buffer.byteLength("确认后插入文本") } } });
                    } else if (path === "/canvas-runtime/resources/normalize") {
                        const input = request.postDataJSON();
                        await route.fulfill({ json: { resource_map: Object.fromEntries(input.resource_ids.map(id => [id, id])), resource_aliases: {} } });
                    } else if (/^\/canvas-runtime\/assets\/[^/]+$/.test(path) && request.method() === "PUT") {
                        const { asset } = request.postDataJSON();
                        if (asset.kind === "video") historyAssetAttempts++;
                        if (holdHistoryAsset && asset.kind === "video") {
                            holdHistoryAsset = false;
                            signalHistoryAsset();
                            await historyAssetResponse;
                            await route.fulfill({ status: 503, json: { error: { code: "fixture_history_asset_failed", message: "历史素材保存暂不可用" } } });
                            return true;
                        }
                        savedAssets.set(asset.id, asset);
                        await route.fulfill({ json: { asset } });
                    } else if (path.endsWith("/commits") && holdHistoryCommit) {
                        holdHistoryCommit = false;
                        signalHistoryCommit();
                        await historyCommitResponse;
                        return false;
                    } else if (/^\/canvas-runtime\/projects\/[^/]+\/assets$/.test(path) && request.method() === "POST") {
                        const input = request.postDataJSON();
                        const asset = savedAssets.get(input.assetId);
                        assert.ok(asset, "项目关联必须使用已保存的素材");
                        await route.fulfill({ json: { asset: { ...asset, category: input.category } } });
                    } else if (/^\/canvas-runtime\/resources\/50[12]\/file$/.test(path)) {
                        const variant = new URL(request.url()).searchParams.get("variant");
                        await route.fulfill({ contentType: "video/mp4", headers: { ETag: variant ? '"501:pb"' : '"501"' }, body: path.includes("501") && !prepared && !variant ? Buffer.from("decode-error-fixture") : preview });
                    } else if (/^\/canvas-runtime\/resources\/50[12]\/playback$/.test(path)) {
                        prepareCalls++; prepared = true;
                        assert.equal(request.headers()["x-canvas-key"], "parity-canvas");
                        await route.fulfill({ json: { resource: resource(path.includes("502") ? "502" : "501") } });
                    } else if (/^\/canvas-runtime\/resources\/50[12]$/.test(path)) {
                        await route.fulfill({ json: { resource: resource(path.endsWith("502") ? "502" : "501") } });
                    } else if (path === "/canvas-runtime/timeline/transcription-models") {
                        await route.fulfill({ json: { models: [{ id: "71", displayName: "测试转写渠道" }] } });
                    } else if (path === "/canvas-runtime/timeline/transcriptions") {
                        transcriptions++;
                        assert.equal(request.postDataJSON().resourceId, "502");
                        assert.equal(request.postDataJSON().model_config_id, "71");
                        await route.fulfill({ status: 202, json: { ...task, id: "831", type: "timeline_transcription", status: "succeeded", resultJson: JSON.stringify({ segments: [{ startMs: 0, endMs: 450, text: "转写字幕" }], srt: "", language: "zh" }) } });
                    } else if (path === "/canvas-runtime/tasks/831") {
                        await route.fulfill({ json: { ...task, id: "831", type: "timeline_transcription", status: "succeeded", resultJson: JSON.stringify({ segments: [{ startMs: 0, endMs: 450, text: "转写字幕" }], srt: "", language: "zh" }) } });
                    } else if (path === "/canvas-runtime/tasks" && !new URL(request.url()).searchParams.get("activeOnly")) {
                        historyReads++;
                        const { resultJson, previewUrl, ...summary } = task;
                        await route.fulfill({ json: [summary] });
                    } else if (path === "/canvas-runtime/tasks/811") {
                        detailReads++;
                        await new Promise(resolve => setTimeout(resolve, 300));
                        if (detailReads === 1) await route.fulfill({ status: 503, json: { error: { code: "fixture_preview_failed", message: "预览暂不可用" } } });
                        else await route.fulfill({ json: task });
                    } else if (path === "/canvas-runtime/style-profiles") {
                        await route.fulfill({ json: { profiles: [] } });
                    } else if (path === "/canvas-runtime/style-profiles/preferences") {
                        await route.fulfill({ json: { favorites: [], recent: [] } });
                    } else return false;
                    return true;
                } });
                await page.goto(`${baseUrl}/canvas-app/canvas/parity-canvas?libtvChrome=1`);
                await page.locator('[data-node-id="video-one"]').waitFor({ state: "visible", timeout: 60000 });
                const exitFocus = page.getByRole("button", { name: "退出专注模式", exact: true });
                if (await exitFocus.isVisible()) await exitFocus.click();
                await page.getByRole("button", { name: "适合屏幕", exact: true }).click();
                await page.locator('[data-node-id="video-one"]').getByRole("button", { name: "播放 测试视频", exact: true }).click();
                await page.locator('[data-node-id="video-one"]').getByRole("button", { name: "播放 测试视频", exact: true }).waitFor({ state: "hidden" });
                await page.waitForFunction(() => { const video = document.querySelector('[data-node-id="video-one"] video'); return video && video.readyState >= 2; }, undefined, { timeout: 15000 }).catch(async error => {
                    throw new Error(`${error.message}\n${JSON.stringify({ prepareCalls, prepared, resourceCalls, errors, unimplemented: [...api.unimplemented], media: await page.locator('[data-node-id="video-one"]').evaluate(element => ({ text: element.innerText, player: element.querySelector("[data-media-player]")?.outerHTML.slice(0, 4000), videos: [...element.querySelectorAll("video")].map(video => ({ src: video.src, ready: video.readyState, error: video.error?.message })) })) })}`, { cause: error });
                });
                assert.ok(prepareCalls >= 1, "解码失败应准备兼容预览");
                const shortcutsButton = page.getByRole("button", { name: "画布快捷键", exact: true });
                await shortcutsButton.click();
                const shortcuts = page.getByRole("dialog", { name: "画布快捷键", exact: true });
                await shortcuts.waitFor();
                const bounds = await shortcuts.boundingBox();
                assert.ok(bounds.x >= 0 && bounds.x + bounds.width <= viewport.width + 1);
                assert.ok(bounds.y >= 0 && bounds.y + bounds.height < viewport.height);
                await page.keyboard.press("Escape");
                await shortcuts.waitFor({ state: "hidden" });
                await page.waitForFunction(element => element === document.activeElement, await shortcutsButton.elementHandle(), { timeout: 3000 });

                await page.getByRole("button", { name: "添加节点", exact: true }).click();
                await page.getByRole("button", { name: "上传", exact: true }).click();
                let dialog = page.getByRole("dialog").filter({ has: page.getByRole("heading", { name: "添加素材", exact: true }) });
                await dialog.waitFor();
                const before = structuredClone(api.document().nodes);
                await dialog.locator('input[type="file"]').setInputFiles([{ name: "one.txt", mimeType: "text/plain", buffer: Buffer.from("第一个文本") }, { name: "two.md", mimeType: "text/markdown", buffer: Buffer.from("第二个文本") }]);
                await dialog.getByRole("button", { name: "移除 two.md", exact: true }).click();
                assert.equal(await dialog.getByRole("button", { name: "添加到画布（1）", exact: true }).isEnabled(), true);
                assert.deepEqual(api.document().nodes, before, "确认前不插入节点");
                await dialog.locator("#canvas-media-url").fill("http://media.example.test/video.mp4");
                assert.equal(await dialog.getByRole("button", { name: "添加链接", exact: true }).isEnabled(), false);
                await dialog.locator("#canvas-media-url").fill("https://media.example.test/video.mp4");
                for (let attempt = 0; attempt < 2; attempt++) {
                    const importButton = dialog.getByRole("button", { name: "添加链接", exact: true });
                    await Promise.all([
                        page.waitForResponse(response => new URL(response.url()).pathname.endsWith("/resources/import")),
                        importButton.click(),
                    ]);
                    await dialog.getByRole("alert").getByText("服务暂时不可用，请稍后重试。", { exact: true }).waitFor();
                    await page.waitForFunction(button => button.getAttribute("aria-busy") === "false", await importButton.elementHandle());
                }
                assert.equal(urlImportKeys.length, 2);
                assert.ok(urlImportKeys[0]);
                assert.equal(urlImportKeys[0], urlImportKeys[1], "相同链接失败重试沿用幂等身份");
                assert.deepEqual(api.document().nodes, before, "链接失败不插入节点");
                await dialog.getByRole("button", { name: /取\s*消/ }).click();
                assert.deepEqual(api.document().nodes, before);
                await page.getByRole("button", { name: "添加节点", exact: true }).click();
                await page.getByRole("button", { name: "上传", exact: true }).click();
                dialog = page.getByRole("dialog").filter({ has: page.getByRole("heading", { name: "添加素材", exact: true }) });
                await dialog.locator('input[type="file"]').setInputFiles({ name: "confirmed.txt", mimeType: "text/plain", buffer: Buffer.from("确认后插入文本") });
                await dialog.getByRole("button", { name: "添加到画布（1）", exact: true }).click();
                await page.waitForFunction(() => [...document.querySelectorAll('[data-node-id]')].length === 3);
                await page.getByRole("button", { name: "画布保存状态：已保存", exact: true }).waitFor();
                await waitForSavedDocument(api, doc => doc.nodes.some(node => node.type === "text" && node.metadata?.content === "确认后插入文本"));
                assert.equal(api.document().nodes.find(node => node.type === "text").metadata.content, "确认后插入文本");
                await page.keyboard.press("Delete");
                await page.waitForFunction(() => document.querySelectorAll('[data-node-id]').length === 2);
                await page.getByRole("button", { name: "画布保存状态：已保存", exact: true }).waitFor();
                await waitForSavedDocument(api, doc => doc.nodes.length === 2);

                await page.getByRole("button", { name: "适合屏幕", exact: true }).click();
                const audio = page.locator('[data-node-id="audio-one"]');
                await audio.hover();
                await audio.click({ position: { x: 70, y: 8 } });
                await page.getByRole("button", { name: "字幕", exact: true }).click();
                const subtitles = page.getByRole("dialog").filter({ hasText: "字幕编辑" });
                await subtitles.waitFor();
                await subtitles.getByRole("button", { name: "导入字幕", exact: true }).click();
                await page.getByRole("menuitem", { name: "导入 SRT", exact: true }).waitFor({ state: "visible" });
                await page.keyboard.press("Escape");
                const textFile = { name: "captions.txt", mimeType: "text/plain", buffer: Buffer.from("第一句\n第二句") };
                await subtitles.locator('input[accept=".txt,text/plain"]').setInputFiles(textFile);
                await page.waitForFunction(() => document.querySelector('.canvas-subtitle-dialog textarea[placeholder="字幕文本"]')?.value === "第一句");
                await subtitles.locator('input[accept=".srt"]').setInputFiles({ name: "invalid.srt", mimeType: "text/plain", buffer: Buffer.from("不是有效字幕") });
                await page.getByText("未解析到有效字幕", { exact: false }).waitFor();
                assert.equal(await subtitles.getByPlaceholder("字幕文本").first().inputValue(), "第一句", "无效导入保留原草稿");
                for (let repeat = 0; repeat < 2; repeat++) {
                    await subtitles.locator('input[accept=".srt"]').setInputFiles({ name: "valid.srt", mimeType: "text/plain", buffer: Buffer.from("1\n00:00:00,000 --> 00:00:00,450\n导入字幕\n") });
                    await page.waitForFunction(() => document.querySelector('.canvas-subtitle-dialog textarea[placeholder="字幕文本"]')?.value === "导入字幕");
                }
                await subtitles.getByRole("button", { name: "刷新转写模型", exact: true }).click();
                await subtitles.getByText("宿主 Whisper 服务", { exact: true }).click();
                await page.locator('.ant-select-dropdown .ant-select-item-option').filter({ hasText: "测试转写渠道" }).click();
                await subtitles.getByRole("button", { name: "自动转写字幕", exact: true }).click();
                await page.waitForFunction(() => document.querySelector('.canvas-subtitle-dialog textarea[placeholder="字幕文本"]')?.value === "转写字幕");
                assert.equal(await subtitles.getByPlaceholder("字幕文本").first().inputValue(), "转写字幕");
                assert.equal(transcriptions, 1);
                await subtitles.getByRole("button", { name: /保\s*存/ }).click();
                await subtitles.waitFor({ state: "hidden" });
                await page.getByRole("button", { name: "画布保存状态：已保存", exact: true }).waitFor();
                await waitForSavedDocument(api, doc => doc.nodes.find(node => node.id === "audio-one")?.metadata?.subtitleEntries?.[0]?.text === "转写字幕");
                assert.equal(api.document().nodes.find(node => node.id === "audio-one").metadata.subtitleEntries[0].text, "转写字幕");

                await page.getByRole("button", { name: "添加节点", exact: true }).click();
                await page.getByRole("button", { name: "从生成历史选择", exact: true }).click();
                const history = page.getByRole("dialog", { name: "从生成历史选择", exact: true });
                const card = history.getByRole("button", { name: "插入视频：历史测试视频", exact: true });
                await card.waitFor();
                const firstRead = historyReads;
                await history.evaluate(dialog => Promise.all(dialog.getAnimations({ subtree: true })
                    .filter(animation => animation.effect?.getTiming().iterations !== Infinity)
                    .map(animation => animation.finished.catch(() => {}))));
                await card.focus();
                await card.hover();
                await card.getByRole("status").filter({ hasText: "服务暂时不可用，请稍后重试。" }).waitFor();
                await card.getByRole("button", { name: "重新加载", exact: true }).press("Enter", { timeout: 5000 });
                await card.locator("video").waitFor({ timeout: 10000 }).catch(async error => {
                    const key = await page.evaluate(async task => {
                        const { generationHistoryVideoStorageKey } = await import("/canvas-app/src/components/canvas/canvas-generation-history-picker.tsx");
                        return generationHistoryVideoStorageKey(task);
                    }, task);
                    const element = await card.evaluate(card => {
                        const box = card.getBoundingClientRect();
                        return { html: card.outerHTML, hovered: card.matches(":hover"), focused: card === document.activeElement,
                            hit: document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2)?.outerHTML };
                    });
                    throw new Error(`${error.message}\n${JSON.stringify({ key, element, resourceCalls, errors })}`, { cause: error });
                });
                await page.waitForFunction(() => { const video = document.querySelector('.ant-modal-content [aria-label="生成视频预览"]'); return video && !video.paused && video.readyState >= 2; });
                assert.equal(api.document().nodes.length, 2, "键盘重试预览不得触发卡片插入");
                await card.focus();
                await page.mouse.move(viewport.width - 4, viewport.height - 4);
                await card.locator("video").waitFor();
                await new Promise(resolve => setTimeout(resolve, 5300));
                assert.ok(historyReads > firstRead, "打开历史时应自动刷新");
                const detailsBeforeSelect = detailReads;
                holdHistoryAsset = true;
                await card.click();
                await historyAssetRequest;
                await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
                assert.equal(await card.getAttribute("aria-busy"), "true", "素材保存期间仍显示插入中");
                await card.press("Enter");
                releaseHistoryAsset();
                await history.getByRole("alert").filter({ hasText: "服务暂时不可用，请稍后重试。" }).waitFor();
                assert.equal(historyAssetAttempts, 1, "保存等待中重复确认不得重复创建素材");
                assert.equal(api.document().nodes.length, 2, "素材保存失败不插入节点");
                assert.equal(await card.getAttribute("aria-busy"), "false");
                holdHistoryCommit = true;
                await card.click();
                await historyCommitRequest;
                await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
                assert.equal(await card.getAttribute("aria-busy"), "true", "画布提交期间仍显示插入中");
                await card.press("Enter");
                assert.equal(await card.getAttribute("aria-busy"), "true");
                releaseHistoryCommit();
                await history.waitFor({ state: "hidden" });
                assert.equal(detailReads, detailsBeforeSelect + 2, "首次失败和明确重试各只读取一次详情");
                await page.getByRole("button", { name: "画布保存状态：已保存", exact: true }).waitFor();
                await waitForSavedDocument(api, doc => doc.nodes.filter(node => node.type === "video").length === 2);
                assert.equal(api.document().nodes.filter(node => node.type === "video").length, 2);
                assert.deepEqual([...api.unimplemented], []);
                await page.screenshot({ path: resolve(`.runtime/browser/canvas-alignment-${viewport.width}.png`) });
                assert.deepEqual(errors, []);
            } catch (error) {
                const pages = context.pages();
                const page = pages[0];
                await page?.screenshot({ path: resolve(`.runtime/browser/canvas-alignment-failure-${viewport.width}.png`) }).catch(() => {});
                const diagnostics = await page?.evaluate(() => ({ dialogs: [...document.querySelectorAll('[role="dialog"]')].map(dialog => ({
                    buttons: [...dialog.querySelectorAll("button")].map(button => button.outerHTML),
                    html: dialog.outerHTML.slice(0, 1500),
                    ancestors: [...(function* () { let parent = dialog.parentElement; while (parent) { yield parent; parent = parent.parentElement; } })()].map(parent => ({ tag: parent.tagName, class: parent.className, ariaHidden: parent.getAttribute("aria-hidden"), inert: parent.inert })),
                })) })).catch(() => null);
                throw new Error(`${error.stack}\nViewport: ${viewport.width}\n${JSON.stringify(diagnostics)}\n${await page?.locator("body").innerText().catch(() => "")}`, { cause: error });
            } finally { await context.close(); }
        }
    } finally { await browser?.close(); await server.close(); }
});



test("独立画布角色直接读取本图设定，重命名保存后恢复且不请求来源目录", { timeout: 120000 }, async () => {
    const baseUrl = "http://127.0.0.1:4182";
    const server = await createServer({ cacheDir: testCacheDir, server: { host: "127.0.0.1", port: 4182, strictPort: true }, logLevel: "error" });
    await server.listen(); let browser;
    try {
        browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH });
        const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, reducedMotion: "reduce" });
        await context.addCookies([{ name: "sd_csrf", value: "browser-fixture", url: baseUrl }]);
        const page = await context.newPage(), requests = [], errors = [];
        page.on("request", request => { if (new URL(request.url()).pathname.startsWith("/api/")) requests.push(request.url()); });
        page.on("pageerror", error => errors.push(error.message));
        const api = await installApi(page, { nodes: [{ ...initial.nodes[0], title: "阿青", metadata: { workflowKind: "character", characterName: "阿青", characterDefinition: { role: "主角", appearance: "青衣", voiceTimbre: "低沉" } } }] });
        await page.goto(`${baseUrl}/canvas-app/canvas/parity-canvas`);
        const node = page.locator('[data-node-id="text-one"]');
        await node.waitFor({ timeout: 60000 });
        await node.dblclick();
        await page.getByRole("heading", { name: "阿青", exact: true }).waitFor();
        assert.ok(await page.getByText("青衣", { exact: true }).isVisible());
        assert.ok(await page.getByText("低沉", { exact: true }).isVisible());
        const details = page.getByRole("dialog");
        await details.locator(".ant-modal-close").click();
        await details.waitFor({ state: "hidden" });
        await page.getByRole("button", { name: "编辑节点名称：阿青", exact: true }).click();
        const title = page.getByRole("textbox", { name: "节点名称", exact: true });
        await title.fill("阿青的角色卡"); await title.press("Enter");
        await waitForSavedDocument(api, document => document.nodes[0].title === "阿青的角色卡");
        assert.equal(Object.hasOwn(api.document(), "projectId"), false);
        assert.equal(api.document().nodes[0].metadata.characterDefinition.appearance, "青衣");
        await page.reload();
        await page.getByRole("button", { name: "编辑节点名称：阿青的角色卡", exact: true }).waitFor();
        assert.equal(requests.some(url => /source-project|character-catalog|character-images|\/episodes/.test(url)), false);
        assert.deepEqual(errors, []);
        await context.close();
    } finally { await browser?.close(); await server.close(); }
});
