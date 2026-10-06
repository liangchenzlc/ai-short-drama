import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdir, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";
import { createServer } from "vite";

// Both original React interfaces use controlled data. Python and actual media
// are verified separately by verify-recycle-python.mjs.
const sourceRoot = process.env.BEEFTV_ROOT || "D:/code/BeefTV";
const sourceUrl = process.env.BEEFTV_BASE_URL || "http://127.0.0.1:3000";
const targetUrl = "http://127.0.0.1:4184";
const diagnoseMenu = process.argv.includes("--diagnose-menu");
const reducedMotion = process.argv.includes("--reduced-motion") ? "reduce" : "no-preference";
const commit = execFileSync("git", ["-C", sourceRoot, "rev-parse", "HEAD"], { encoding: "utf8" }).trim();
assert.equal(commit, "4ca2a65a7780a8dfcaaa86c33679f84fb04e055c");
assert.equal(execFileSync("git", ["-C", sourceRoot, "status", "--porcelain"], { encoding: "utf8" }).trim(), "");
const output = resolve(".runtime/recycle-parity");
await mkdir(output, { recursive: true });
const now = "2026-01-02T03:04:00.000Z";
async function fixtures(page, kind) {
    const document = {
        id: "parity-recycle", workspaceProjectId: "9007199254740995", revision: kind === "source" ? 1 : "1",
        folderId: "parity-folder", title: "对照作品", createdAt: now, updatedAt: now,
        nodes: [{ id: "text-one", type: "text", title: "文字节点", position: { x: 0, y: 0 }, width: 320, height: 220, metadata: { content: "回收站恢复对照" } }],
        connections: [], chatSessions: [], activeChatId: null, directorScenes: [],
        viewport: { x: 0, y: 0, k: 1 }, backgroundMode: "dots", showImageInfo: false,
    };
    let folders = [{ id: "parity-folder", name: "对照文件夹", createdAt: now, updatedAt: now }];
    let active = true;
    let purged = false;
    let archiveKey;
    const unknown = new Set();
    const summary = () => ({ id: "9007199254740993", project_id: document.workspaceProjectId, source_key: document.id, title: document.title,
        row_version: String(document.revision), schema_version: 1, created_at: now, updated_at: now });
    await page.route(url => url.pathname.startsWith("/api/"), async route => {
        const request = route.request();
        const path = new URL(request.url()).pathname.replace(/^\/api(?:\/v1(?:\/canvas-runtime)?)?/, "");
        let body;
        let missing = false;
        if (path === "/auth/me") body = { user: { id: "111", username: "parity", display_name: "画布对照", email: "parity@example.test" } };
        else if (path === "/workspace/bootstrap") body = {
            contractVersion: 1, profile: "local", storageMode: "local", capabilities: { localAssets: true, providerCalls: true },
            user: { id: "111", username: "parity", displayName: "画布对照", role: "user", status: "active" },
            workspace: { id: "111", name: "画布对照", owner: "local", storage: "sqlite" },
            features: { shortDramaEnabled: true, taskCenterEnabled: true, customChannelsEnabled: true, frontendModelsEnabled: false, pluginCenterEnabled: true, systemPluginsVisibleToUsers: true },
        };
        else if (path === "/workspace/model-config") body = kind === "source" ? { config: { channels: [] }, revision: 0, health: "ready", source: "builtin+local" } : { row_version: "0", preferences: {}, models: [] };
        else if (path === "/canvas-folders") body = { folders };
        else if (path === "/recycle-bin") body = { items: active || purged ? [] : [{ source_key: document.id, project_id: document.workspaceProjectId,
            archive_key: archiveKey, deleted_at: now, source_document: structuredClone(document) }] };
        else if (path.endsWith("/recycle-purge")) { purged = true; body = { source_key: document.id, archive_key: archiveKey, state: "purged" }; }
        else if (path === "/canvas-folders/parity-folder" && request.method() === "DELETE") { folders = []; body = { id: "parity-folder" }; }
        else if (path === "/canvas-projects") body = { projects: active ? [document] : [], page: 1, pageSize: 50, total: active ? 1 : 0, hasMore: false };
        else if (path === "/canvas-workspace") body = { items: active ? [{ ...summary(), folder_id: document.folderId, node_count: 1, preview_nodes: document.nodes, source_document: document }] : [], page: 1, page_size: 50, total: active ? 1 : 0, has_more: false };
        else if (path === "/canvas-workspace/resolve/parity-recycle") { body = summary(); missing = !active; }
        else if (path.startsWith("/canvas-write-receipts/")) missing = true;
        else if (path === "/canvas-projects/parity-recycle" || path === "/projects/9007199254740995/canvases/9007199254740993") {
            if (request.method() === "DELETE") { active = false; archiveKey = request.headers()["idempotency-key"]; body = kind === "source" ? { id: document.id } : { archived_canvas_id: summary().id, next_canvas_id: null, project_archived: true }; }
            else body = { project: document };
        }
        else if (path.endsWith("/my-document")) body = { ...summary(), source_document: document };
        else if (path.endsWith("/recycle-restore")) { active = true; document.revision = "3"; document.folderId = undefined; body = summary(); }
        else if (path.endsWith("/recycle-status")) body = { source_key: document.id, project_id: document.workspaceProjectId,
            archive_key: new URL(request.url()).searchParams.get("archive_key"), expected_row_version: "1", committed_row_version: "2", state: active ? "superseded" : "archived" };
        else if (path === "/tasks") body = { tasks: [], total: 0, page: 1, pageSize: 50, hasMore: false };
        else if (path.endsWith("/events")) return route.fulfill({ contentType: "text/event-stream; charset=utf-8", body: ": connected\n\n" });
        else if (path === "/assistant/status") body = { available: false, reason: "model_not_configured" };
        else if (path === "/assistant/ui-session") body = { token: "parity-fixture-only", expiresAt: "2100-01-01T00:00:00Z" };
        else if (path === "/assistant/sessions") body = { currentSessionId: null, sessions: [] };
        else if (path === "/assistant/history") body = { sessionId: "", turns: [] };
        else if (path === "/assets") body = { assets: [], page: 1, pageSize: 100, total: 0, hasMore: false };
        else { unknown.add(`${request.method()} ${path}`); missing = true; }
        if (missing) return route.fulfill({ status: 404, json: kind === "source" ? { code: 404, reason: "not_found", msg: "不存在" } : { error: { code: "not_found", message: "不存在" } } });
        await route.fulfill({ json: kind === "source" ? { code: 0, data: body, msg: "ok" } : body });
    });
    return unknown;
}
async function settleAnimations(page) {
    await page.evaluate(async () => {
        await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
        await Promise.all(document.getAnimations().filter(animation => Number.isFinite(animation.effect?.getComputedTiming().endTime))
            .map(animation => animation.finished.catch(() => undefined)));
        await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    });
}
async function capture(page, kind, name) {
    await page.mouse.move(1400, 850);
    await settleAnimations(page);
    await page.evaluate(async () => { await document.fonts.ready; });
    let previous;
    let screenshot;
    let stable = 0;
    for (let i = 0; i < 15; i++) {
        screenshot = await page.screenshot({ caret: "hide" });
        stable = previous?.equals(screenshot) ? stable + 1 : 0;
        if (stable >= 2) break;
        previous = screenshot;
        await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    }
    assert.ok(stable >= 2, `${kind}/${name}: screenshot did not settle`);
    await writeFile(resolve(output, `${name}.${kind}.png`), screenshot);
    return page.evaluate(() => [...document.querySelectorAll('header, .collection-content, .recycle-bin-dialog, .recycle-bin-dialog *, .ant-dropdown-menu, .ant-dropdown-menu *')].map(element => {
        const box = element.getBoundingClientRect();
        const style = getComputedStyle(element);
        return [element.tagName, box.x, box.y, box.width, box.height, style.font, style.color, style.backgroundColor];
    }));
}
const server = await createServer({ server: { host: "127.0.0.1", port: 4184, strictPort: true }, logLevel: "error" });
await server.listen();
let browser;
let page;
const report = { commit, scope: "Fixture-driven source and target UI only; folder-restore source defect is recorded separately, not counted as equal", reducedMotion, browser: "", scenarios: {}, folderRestore: {}, errors: {}, unknown: {} };
try {
    browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH });
    report.browser = browser.version();
    for (const kind of ["source", "target"]) {
        const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1, colorScheme: "dark", reducedMotion, locale: "zh-CN", timezoneId: "UTC" });
        const base = kind === "source" ? sourceUrl : targetUrl;
        await context.addCookies([{ name: "sd_csrf", value: "parity-fixture-only", url: base }]);
        // The fixed source browser build reads folder fixtures from localStorage;
        // the target uses the same fixture through its authenticated service.
        await context.addInitScript(({ now }) => {
            localStorage.setItem("infinite-canvas:active-user-scope", "111");
            if (!localStorage.getItem("recycle-parity-seeded")) {
                localStorage.setItem("infinite-canvas:canvas_folders:user:111", JSON.stringify([{ id: "parity-folder", name: "对照文件夹", createdAt: now, updatedAt: now }]));
                localStorage.setItem("recycle-parity-seeded", "1");
            }
        }, { now });
        page = await context.newPage();
        await page.clock.setFixedTime(new Date(now));
        page.setDefaultTimeout(20000);
        report.errors[kind] = [];
        page.on("pageerror", error => report.errors[kind].push(error.message));
        const unknown = await fixtures(page, kind);
        const record = async name => { report.scenarios[name] ??= {}; report.scenarios[name][kind] = await capture(page, kind, name); };
        try {
            await page.goto(`${base}${kind === "source" ? "" : "/canvas-app"}/canvas/parity-recycle`);
            await page.locator('[data-node-id="text-one"]').waitFor({ timeout: 90000 });
            await page.goto(`${base}${kind === "source" ? "" : "/canvas-app"}/canvas`);
            await page.getByRole("button", { name: "对照文件夹 文件夹操作", exact: true }).waitFor({ timeout: 90000 });
            await record("folder-library");
            if (!diagnoseMenu) {
                await page.getByRole("button", { name: "回收站", exact: true }).click();
                await page.getByText("回收站是空的", { exact: true }).waitFor();
                await record("recycle-empty");
                await page.keyboard.press("Escape");
            }
            const folderMenu = page.getByRole("button", { name: "对照文件夹 文件夹操作", exact: true });
            await page.evaluate(() => {
                window.__parityMenuTrace = [];
                for (const eventName of ["pointerdown", "pointerup", "click"]) {
                    document.addEventListener(eventName, event => {
                        if (!event.target.closest?.(".libtv-folder-card-more")) return;
                        const button = document.querySelector(".libtv-folder-card-more");
                        window.__parityMenuTrace.push({ event: eventName, box: button.getBoundingClientRect().toJSON(), transform: getComputedStyle(button).transform });
                    }, { capture: true });
                }
            });
            await folderMenu.hover();
            await settleAnimations(page);
            await folderMenu.click();
            await page.getByRole("menuitem", { name: "删除文件夹", exact: true }).waitFor();
            if (diagnoseMenu) {
                report.menuDiagnostics ??= {};
                report.menuDiagnostics[kind] = await page.evaluate(async () => {
                    const describe = element => {
                        if (!element) return null;
                        const style = getComputedStyle(element);
                        const rect = element.getBoundingClientRect().toJSON();
                        return { tag: element.tagName, classes: element.className, inline: element.getAttribute("style"), rect,
                            computed: Object.fromEntries(["position", "top", "left", "right", "bottom", "transform", "transition", "animation", "contain", "contentVisibility", "overflow", "height", "margin", "padding"].map(name => [name, style[name]])),
                            scroll: [element.scrollLeft, element.scrollTop, element.scrollWidth, element.scrollHeight],
                            animations: element.getAnimations().map(animation => ({ playState: animation.playState, timing: animation.effect?.getComputedTiming(), keyframes: animation.effect?.getKeyframes() })) };
                    };
                    const states = [];
                    for (let frame = 0; frame < 24; frame++) {
                        await new Promise(resolve => requestAnimationFrame(resolve));
                        const popup = document.querySelector(".ant-dropdown");
                        const ancestors = [];
                        for (let element = popup?.parentElement; element; element = element.parentElement) ancestors.push(describe(element));
                        states.push({ frame, time: performance.now(), anchor: describe(document.querySelector(".libtv-folder-card-more")), popup: describe(popup), ancestors,
                            viewport: [innerWidth, innerHeight, scrollX, scrollY], document: describe(document.documentElement) });
                    }
                    return states;
                });
                await capture(page, kind, "menu-diagnostic");
                continue;
            }
            // rc-trigger realigns when the opening animation finishes. Keep the
            // pointer on the trigger until that callback has used its settled box.
            await settleAnimations(page);
            await page.getByRole("menuitem", { name: "删除文件夹", exact: true }).hover();
            await record("folder-menu");
            report.menuPositions ??= {};
            report.menuPositions[kind] = await page.evaluate(() => ({ trace: window.__parityMenuTrace,
                anchor: document.querySelector(".libtv-folder-card-more").getBoundingClientRect().toJSON(),
                popup: document.querySelector(".ant-dropdown").getAttribute("style") }));
            await page.getByRole("menuitem", { name: "删除文件夹", exact: true }).click();
            await page.getByText("文件夹及其中 1 个项目已移入回收站", { exact: true }).waitFor({ state: "attached" });
            await page.locator(".ant-message-notice").waitFor({ state: "hidden" });
            const catalog = kind === "target" ? page.waitForResponse(response => new URL(response.url()).pathname.endsWith("/recycle-bin")) : Promise.resolve();
            await page.getByRole("button", { name: "回收站", exact: true }).click();
            await catalog;
            await page.getByRole("button", { name: "恢复到项目列表", exact: true }).waitFor();
            const dialog = page.getByRole("dialog", { name: "回收站", exact: true });
            await dialog.locator(".recycle-bin-card").waitFor();
            await record("recycle-default");
            await dialog.getByRole("checkbox", { name: "全选回收站项目", exact: true }).check();
            await record("recycle-selected");
            await dialog.getByRole("button", { name: "彻底删除", exact: true }).click();
            await page.getByRole("alertdialog", { name: "确认彻底删除？", exact: true }).waitFor();
            await record("recycle-confirm-delete");
            await page.keyboard.press("Escape");
            await dialog.getByRole("button", { name: "恢复到项目列表", exact: true }).click();
            await dialog.getByText("回收站是空的", { exact: true }).waitFor();
            await page.keyboard.press("Escape");
            const restored = page.getByRole("button", { name: "对照作品 画布操作", exact: true });
            if (kind === "target") await restored.waitFor();
            report.folderRestore[kind] = { visibleCanvasCards: await restored.count(), geometry: await capture(page, kind, "restored-list-known-source-defect") };
        } catch (error) {
            report.failureState = await page.evaluate(() => ({
                now: Date.now(), performance: performance.now(),
                popups: [...document.querySelectorAll('.ant-dropdown')].map(element => {
                    const rect = element.getBoundingClientRect();
                    const style = getComputedStyle(element);
                    return { classes: element.className, inline: element.getAttribute('style'), x: rect.x, y: rect.y, width: rect.width, height: rect.height,
                        top: style.top, left: style.left, transform: style.transform, position: style.position, display: style.display, visibility: style.visibility };
                }),
            }));
            await page.screenshot({ path: resolve(output, `failure.${kind}.png`) });
            throw error;
        } finally {
            report.unknown[kind] = [...unknown];
            await context.close();
        }
    }
    for (const item of Object.values(report.scenarios)) item.geometryEqual = JSON.stringify(item.source) === JSON.stringify(item.target);
} finally {
    await writeFile(resolve(output, "report.json"), JSON.stringify(report, null, 2));
    await browser?.close();
    await server.close();
}
if (diagnoseMenu) {
    const positions = Object.fromEntries(Object.entries(report.menuDiagnostics).map(([kind, states]) => [kind, { first: states[0].popup, last: states.at(-1).popup }]));
    process.stdout.write(JSON.stringify(positions) + "\n");
    process.exit(0);
}
const packageRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const compared = JSON.parse(execFileSync(resolve(packageRoot, "../../backend/.venv/Scripts/python.exe"), ["-c", `
import json, sys
from pathlib import Path
from PIL import Image, ImageChops
root = Path(sys.argv[1])
report = json.loads((root / 'report.json').read_text(encoding='utf-8'))
for name, item in report['scenarios'].items():
    source = Image.open(root / (name + '.source.png')).convert('RGB')
    target = Image.open(root / (name + '.target.png')).convert('RGB')
    diff = ImageChops.difference(source, target)
    diff.save(root / (name + '.diff.png'))
    pixels = diff.tobytes()
    item['changedPixels'] = sum(any(pixels[i:i+3]) for i in range(0, len(pixels), 3))
(root / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
sys.stdout.write(json.dumps({name: {'geometryEqual': item['geometryEqual'], 'changedPixels': item['changedPixels']} for name, item in report['scenarios'].items()}))
`, output], { encoding: "utf8" }));
process.stdout.write(JSON.stringify(compared) + "\n");
for (const [name, item] of Object.entries(compared)) {
    assert.equal(item.geometryEqual, true, `${name}: geometry differs`);
    assert.equal(item.changedPixels, 0, `${name}: pixels differ`);
}
assert.deepEqual(report.errors, { source: [], target: [] });
assert.deepEqual(report.unknown, { source: [], target: [] });
assert.equal(report.folderRestore.source.visibleCanvasCards, 0, "Recheck the source stale-folder finding");
assert.equal(report.folderRestore.target.visibleCanvasCards, 1, "Hosted restore must retain the existing work");
