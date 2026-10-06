import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { chromium } from "playwright";
import { createServer } from "vite";
import { verifyCopyIdentity } from "./verify-copy-identity-python.mjs";
import { verifyNormalization } from "./verify-normalization-python.mjs";
import { verifyNormalizationRecovery } from "./verify-normalization-recovery-python.mjs";
import { verifyCreation } from "./verify-creation-python.mjs";
import { verifyCreationRecovery } from "./verify-creation-recovery-python.mjs";
import { verifyCreationSession } from "./verify-creation-session-python.mjs";

let input = "";
for await (const chunk of process.stdin) input += chunk;
const config = JSON.parse(input);
const origin = "http://127.0.0.1:4187";
const output = resolve(".runtime/resource-python-browser");
const report = { reduced_motion: "no-preference", image_uploaded: false, image_saved: false, image_restored: false, downloaded_from_storage: false, library_classified: false, library_unclassified_after_delete: false, page_errors: [], failed_routes: [] };
await mkdir(output, { recursive: true });
const server = await createServer({ server: { host: "127.0.0.1", port: 4187, strictPort: true, proxy: { "/api": { target: config.apiUrl, changeOrigin: true } } }, logLevel: "silent" });
await server.listen();
let browser;
let page;
const responseDetails = [];
function recordFailure(response) {
    if (response.status() < 400 || !new URL(response.url()).pathname.startsWith("/api/")) return;
    const entry = { path: new URL(response.url()).pathname, status: response.status() };
    report.failed_routes.push(entry);
    responseDetails.push(response.json().then(body => { entry.code = body.error?.code; }).catch(() => {}));
}
async function read(response) {
    assert.ok(response.ok(), `HTTP ${response.status()} ${new URL(response.url()).pathname}: ${await response.text()}`);
    return response.json();
}
async function settledMenuItem(locator) {
    await locator.waitFor();
    // AntD can still align/scale the popup after the item has entered the DOM.
    // Wait for the actual hit target before moving the pointer into a submenu.
    await locator.evaluate(element => new Promise((resolve, reject) => {
        let previous = "";
        let stable = 0;
        const deadline = performance.now() + 5000;
        const frame = () => {
            const rect = element.getBoundingClientRect();
            const hit = document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2);
            const signature = JSON.stringify(rect.toJSON());
            stable = signature === previous && element.contains(hit) ? stable + 1 : 0;
            previous = signature;
            if (stable >= 8) resolve();
            else if (performance.now() > deadline) reject(new Error("原版菜单位置或命中区域未稳定"));
            else requestAnimationFrame(frame);
        };
        requestAnimationFrame(frame);
    }));
    return locator;
}
try {
    browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH });
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, reducedMotion: report.reduced_motion });
    context.setDefaultTimeout(30000);
    await read(await context.request.post(`${origin}/api/v1/auth/login`, { headers: { Origin: origin }, data: { username: config.username, password: config.password } }));
    const csrf = (await context.cookies()).find(cookie => cookie.name === "sd_csrf");
    await context.setExtraHTTPHeaders({ Origin: origin, "X-CSRF-Token": csrf.value });
    const project = await read(await context.request.post(`${origin}/api/v1/projects`, { headers: { "Idempotency-Key": "browser-resource-project" }, data: { name: "真实媒体上传", aspect: "16:9", workspace_mode: "infinite_canvas" } }));
    const path = `${origin}/api/v1/projects/${project.id}/canvases/${project.primary_canvas_id}`;
    const initial = await read(await context.request.get(`${path}/my-document`));
    page = await context.newPage();
    page.on("pageerror", error => report.page_errors.push(error.message));
    page.on("response", recordFailure);
    await page.goto(`${origin}/canvas-app/canvas/${initial.source_key}`, { timeout: 60000 });
    const fileInput = page.locator('input[type="file"][accept^="image/"]');
    await fileInput.waitFor({ state: "attached", timeout: 60000 });
    const resourceResponse = page.waitForResponse(response => new URL(response.url()).pathname === "/api/v1/canvas-runtime/resources" && response.request().method() === "POST");
    await fileInput.setInputFiles({ name: "browser-upload.png", mimeType: "image/png", buffer: Buffer.from(config.image, "base64") });
    const resource = (await read(await resourceResponse)).resource;
    report.image_uploaded = true;
    let saved;
    for (let attempt = 0; attempt < 80; attempt++) {
        const result = await read(await context.request.get(`${path}/my-document`));
        saved = result.source_document.nodes.find(node => node.metadata?.storageKey === `resource:${resource.id}` && node.metadata?.assetId);
        if (saved) break;
        await new Promise(resolve => setTimeout(resolve, 250));
    }
    await Promise.all(responseDetails);
    assert.ok(saved, `source upload must persist its resource and library binding: ${JSON.stringify(report.failed_routes)}`);
    report.image_saved = true;
    // A fresh context has neither the upload Blob cache nor IndexedDB records.
    const fresh = await browser.newContext({ viewport: { width: 1440, height: 900 }, reducedMotion: report.reduced_motion, storageState: { cookies: await context.cookies(), origins: [] } });
    fresh.setDefaultTimeout(30000);
    page = await fresh.newPage();
    page.on("pageerror", error => report.page_errors.push(error.message));
    page.on("response", response => {
        if (response.ok() && new URL(response.url()).pathname === `/api/v1/canvas-runtime/resources/${resource.id}/file`) report.downloaded_from_storage = true;
        recordFailure(response);
    });
    await page.goto(`${origin}/canvas-app/canvas/${initial.source_key}`);
    const node = page.locator(`[data-node-id="${saved.id}"]`);
    await node.waitFor({ state: "visible" });
    await node.locator("img").first().waitFor({ state: "visible" });
    await page.waitForFunction(id => {
        const image = document.querySelector(`[data-node-id="${id}"] img`);
        return image instanceof HTMLImageElement && image.complete && image.naturalWidth === 37 && image.naturalHeight === 19;
    }, saved.id);
    report.image_restored = true;
    assert.ok(report.downloaded_from_storage, "fresh browser must read the real MinIO-backed resource endpoint");
    await page.goto(`${origin}/canvas-app/assets`);
    await page.getByRole("heading", { name: "个人资产库", exact: true }).waitFor();
    const libraryPage = await read(await fresh.request.get(`${origin}/api/v1/canvas-runtime/assets?page=1&status=active`));
    report.library_api_ids = libraryPage.assets.map(asset => asset.id);
    assert.ok(report.library_api_ids.includes(saved.metadata.assetId), "registered media must be in the canonical library page");
    await page.getByRole("button", { name: "更多素材操作", exact: true }).waitFor();
    await page.getByRole("button", { name: "新建", exact: true }).click();
    await page.getByRole("menuitem", { name: "新建文件夹", exact: true }).click();
    const creation = page.getByRole("dialog", { name: "新建分类", exact: true });
    await creation.getByPlaceholder("例如：角色参考、场景灵感").fill("浏览器分类");
    const [folderSaved] = await Promise.all([
        page.waitForResponse(response => new URL(response.url()).pathname === "/api/v1/canvas-runtime/asset-folders" && response.request().method() === "POST"),
        creation.getByRole("button", { name: /^保\s*存$/ }).click(),
    ]);
    const folder = (await read(folderSaved)).folder;
    await creation.waitFor({ state: "hidden" });
    await page.getByRole("button", { name: "筛选资产", exact: true }).click();
    await page.getByRole("button", { name: /^浏览器分类/ }).waitFor();
    await page.getByRole("button", { name: "更多素材操作", exact: true }).click();
    await (await settledMenuItem(page.getByRole("menuitem", { name: /^移动到分类(?: right)?$/ }))).hover();
    const destination = await settledMenuItem(page.getByRole("menuitem", { name: "浏览器分类", exact: true }));
    const [moved] = await Promise.all([
        page.waitForResponse(response => new URL(response.url()).pathname === "/api/v1/canvas-runtime/assets/folder" && response.request().method() === "PATCH"),
        destination.click(),
    ]);
    const moveResult = await read(moved);
    assert.equal(moveResult.folderId, folder.id);
    await page.reload();
    await page.getByRole("button", { name: "筛选资产", exact: true }).click();
    await page.getByRole("button", { name: /^浏览器分类/ }).click();
    await page.getByRole("button", { name: "更多素材操作", exact: true }).waitFor();
    report.library_classified = true;
    await page.getByRole("button", { name: /^浏览器分类/ }).click({ button: "right" });
    await page.getByRole("menuitem", { name: "重命名", exact: true }).click();
    const rename = page.getByRole("textbox", { name: "重命名分类 浏览器分类" });
    await rename.fill("已重命名分类");
    const [renamed] = await Promise.all([
        page.waitForResponse(response => new URL(response.url()).pathname === `/api/v1/canvas-runtime/asset-folders/${folder.id}` && response.request().method() === "PATCH"),
        rename.press("Enter"),
    ]);
    assert.equal((await read(renamed)).folder.name, "已重命名分类");
    await page.getByRole("button", { name: /^已重命名分类/ }).click({ button: "right" });
    await page.getByRole("menuitem", { name: "删除", exact: true }).click();
    const [deleted] = await Promise.all([
        page.waitForResponse(response => new URL(response.url()).pathname === `/api/v1/canvas-runtime/asset-folders/${folder.id}` && response.request().method() === "DELETE"),
        page.locator(".assets-folder-delete-confirm").getByRole("button", { name: "删除分类", exact: true }).click(),
    ]);
    await read(deleted);
    await page.reload();
    await page.getByRole("button", { name: "筛选资产", exact: true }).click();
    await page.getByRole("button", { name: /^未分类/ }).click();
    await page.getByRole("button", { name: "更多素材操作", exact: true }).waitFor();
    const restoredAsset = (await read(await fresh.request.get(`${origin}/api/v1/canvas-runtime/assets/${moveResult.assetIds[0]}`))).asset;
    assert.equal(restoredAsset.folderId, "");
    assert.equal(restoredAsset.data.storageKey, `resource:${resource.id}`);
    report.library_unclassified_after_delete = true;
    // Prepare an unreferenced archived resource through real authenticated APIs,
    // then use the unchanged source dialogs for restore and permanent deletion.
    await fresh.setExtraHTTPHeaders({ Origin: origin, "X-CSRF-Token": csrf.value });
    const disposable = (await read(await fresh.request.post(`${origin}/api/v1/canvas-runtime/resources`, {
        headers: { "X-Idempotency-Key": "browser-disposable-resource" },
        multipart: { kind: "image", file: { name: "disposable.png", mimeType: "image/png", buffer: Buffer.from(config.image, "base64") } },
    }))).resource;
    const disposableKey = "browser-disposable-asset";
    await read(await fresh.request.put(`${origin}/api/v1/canvas-runtime/assets/${disposableKey}`, {
        data: { asset: { id: disposableKey, kind: "image", title: "浏览器删除验证", status: "archived", data: { storageKey: `resource:${disposable.id}`, dataUrl: "", width: disposable.width, height: disposable.height, bytes: disposable.size, mimeType: disposable.mimeType } } },
    }));
    await page.reload();
    await page.getByRole("button", { name: "恢复已删除素材", exact: true }).click();
    const recovery = page.getByRole("dialog", { name: "恢复已删除素材", exact: true });
    const [recovered] = await Promise.all([
        page.waitForResponse(response => new URL(response.url()).pathname === `/api/v1/canvas-runtime/assets/${disposableKey}` && response.request().method() === "PUT"),
        recovery.getByRole("button", { name: /^恢\s*复$/ }).click(),
    ]);
    assert.equal((await read(recovered)).asset.status, "confirmed");
    await recovery.getByRole("button", { name: /close|关闭/i }).click();
    await recovery.waitFor({ state: "hidden" });
    report.library_restored = true;
    const disposableCard = page.locator("article.asset-library-card").filter({ has: page.getByRole("heading", { name: "浏览器删除验证", exact: true }) });
    await disposableCard.getByRole("button", { name: "更多素材操作", exact: true }).click();
    await (await settledMenuItem(page.getByRole("menuitem", { name: "彻底删除", exact: true }))).click();
    const deletionDialog = page.getByRole("dialog", { name: "彻底删除素材", exact: true });
    const [assetDeleted] = await Promise.all([
        page.waitForResponse(response => new URL(response.url()).pathname === `/api/v1/canvas-runtime/assets/${disposableKey}` && response.request().method() === "DELETE"),
        deletionDialog.getByRole("button", { name: "彻底删除", exact: true }).click(),
    ]);
    assert.equal((await read(assetDeleted)).id, disposableKey);
    await deletionDialog.waitFor({ state: "hidden" });
    await page.reload();
    assert.equal(await fresh.request.get(`${origin}/api/v1/canvas-runtime/assets/${disposableKey}`).then(response => response.status()), 404);
    assert.equal(await fresh.request.get(`${origin}/api/v1/canvas-runtime/resources/${disposable.id}/file`).then(response => response.status()), 404);
    assert.equal(await page.getByRole("heading", { name: "浏览器删除验证", exact: true }).count(), 0);
    report.library_deleted = true;
    report.deleted_resource_id = disposable.id;
    // A live canvas binding must keep both the original asset and real bytes.
    await page.getByRole("button", { name: "更多素材操作", exact: true }).click();
    await (await settledMenuItem(page.getByRole("menuitem", { name: "彻底删除", exact: true }))).click();
    const [blocked] = await Promise.all([
        page.waitForResponse(response => new URL(response.url()).pathname === `/api/v1/canvas-runtime/assets/${saved.metadata.assetId}` && response.request().method() === "DELETE"),
        deletionDialog.getByRole("button", { name: "彻底删除", exact: true }).click(),
    ]);
    assert.equal(blocked.status(), 409);
    assert.equal((await blocked.json()).error.code, "canvas_asset_in_use");
    await deletionDialog.getByRole("button", { name: /^取\s*消$/ }).click();
    await read(await fresh.request.get(`${origin}/api/v1/canvas-runtime/assets/${saved.metadata.assetId}`));
    const kept = await fresh.request.get(`${origin}/api/v1/canvas-runtime/resources/${resource.id}/file`);
    assert.deepEqual(await kept.body(), Buffer.from(config.image, "base64"));
    report.library_delete_blocked = true;
    await verifyCreation({ browser, context, origin, read, original: initial, saved, resource, report, recordFailure });
    await verifyCreationRecovery({ browser, context, origin, read, saved, report, recordFailure });
    await verifyCreationSession({ browser, context, origin, read, config, original: initial, saved, report, recordFailure });
    await verifyCopyIdentity({ browser, context, origin, read, saved, resource, report, recordFailure });
    await verifyNormalization({ browser, context, origin, read, saved, resource, report, recordFailure });
    await verifyNormalizationRecovery({ browser, context, origin, read, saved, resource, report, recordFailure });
    assert.deepEqual(report.page_errors, []);
} catch (error) {
    if (page) await page.screenshot({ path: resolve(output, "failure.png") }).catch(() => {});
    if (page) await writeFile(resolve(output, "failure-dom.txt"), await page.locator("body").ariaSnapshot()).catch(() => {});
    if (page) await writeFile(resolve(output, "failure-menus.json"), JSON.stringify(await page.locator('.ant-dropdown, [role="menuitem"]').evaluateAll(elements => elements.map(element => {
        const style = getComputedStyle(element);
        return { text: element.textContent, className: element.className, rect: element.getBoundingClientRect().toJSON(), opacity: style.opacity, visibility: style.visibility, display: style.display, pointerEvents: style.pointerEvents, zIndex: style.zIndex, transform: style.transform };
    })), null, 2)).catch(() => {});
    throw error;
} finally {
    await Promise.all(responseDetails);
    await writeFile(resolve(output, "report.json"), JSON.stringify(report, null, 2) + "\n");
    await browser?.close();
    await server.close();
}
process.stdout.write(JSON.stringify(report));
