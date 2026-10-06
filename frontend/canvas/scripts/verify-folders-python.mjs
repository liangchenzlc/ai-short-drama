import assert from "node:assert/strict";
import { mkdir, readFile } from "node:fs/promises";
import { resolve } from "node:path";
import { unzipSync, strFromU8 } from "fflate";
import { chromium } from "playwright";
import { createServer } from "vite";

let input = "";
for await (const chunk of process.stdin) input += chunk;
const config = JSON.parse(input);
const origin = "http://127.0.0.1:4189";
const folderPath = `${origin}/api/v1/canvas-runtime/canvas-folders`;
const output = resolve(".runtime/folders-python-browser");
const report = { step: "start", page_errors: [], failed_routes: [] };
await mkdir(output, { recursive: true });
const server = await createServer({ server: { host: "127.0.0.1", port: 4189, strictPort: true, proxy: { "/api": { target: config.apiUrl, changeOrigin: true } } }, logLevel: "silent" });
await server.listen();
let browser;
let page;
function observe(target) {
    target.on("pageerror", error => report.page_errors.push(error.message));
    target.on("response", response => {
        const path = new URL(response.url()).pathname;
        if (response.status() >= 400 && path.startsWith("/api/")) report.failed_routes.push({ path, status: response.status() });
    });
}
async function read(response) {
    assert.ok(response.ok(), `HTTP ${response.status()} ${new URL(response.url()).pathname}: ${await response.text()}`);
    return response.json();
}
async function login(username) {
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    context.setDefaultTimeout(20000);
    await read(await context.request.post(`${origin}/api/v1/auth/login`, { headers: { Origin: origin }, data: { username, password: config.password } }));
    const csrf = (await context.cookies()).find(cookie => cookie.name === "sd_csrf");
    await context.setExtraHTTPHeaders({ Origin: origin, "X-CSRF-Token": csrf.value });
    return context;
}
async function eventually(readValue, predicate) {
    const deadline = Date.now() + 20000;
    do {
        const value = await readValue();
        if (predicate(value)) return value;
        await new Promise(resolve => setTimeout(resolve, 100));
    } while (Date.now() < deadline);
    assert.fail(`操作未完成：${report.step}`);
}
async function library(context) {
    const target = await context.newPage();
    observe(target);
    await target.goto(`${origin}/canvas-app/canvas`);
    await target.getByRole("button", { name: "新建文件夹", exact: true }).waitFor({ timeout: 60000 });
    return target;
}
async function move(target, title, folder) {
    await target.getByRole("button", { name: `${title} 画布操作`, exact: true }).click();
    const trigger = target.getByText("移动至文件夹", { exact: true });
    await settleMenu(trigger);
    await trigger.hover();
    const destination = target.getByRole("menuitem", { name: folder, exact: true });
    await settleMenu(destination);
    await destination.click();
    await target.getByText(folder === "未分类" ? "已移出文件夹" : "已移动到文件夹", { exact: true }).waitFor({ state: "attached" });
}
async function settleMenu(locator) {
    await locator.waitFor({ state: "visible" });
    // Ancestor animations can move the submenu away from a just-hovered cursor.
    // Let the source motion finish; do not disable it or click invisible entries.
    await locator.evaluate(async element => {
        const animations = new Set();
        for (let parent = element; parent; parent = parent.parentElement) {
            for (const animation of parent.getAnimations()) animations.add(animation);
        }
        await Promise.all([...animations].map(animation => animation.finished.catch(() => undefined)));
    });
}
try {
    browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH });
    const owner = await login(config.username);
    const project = await read(await owner.request.post(`${origin}/api/v1/projects`, {
        headers: { "Idempotency-Key": "folder-browser-project" }, data: { name: "文件夹双画布项目", aspect: "16:9", workspace_mode: "infinite_canvas" },
    }));
    const path = `${origin}/api/v1/projects/${project.id}/canvases/${project.primary_canvas_id}`;
    const initial = await read(await owner.request.get(`${path}/my-document`));
    const secondary = await read(await owner.request.post(`${origin}/api/v1/projects/${project.id}/canvases`, {
        headers: { "Idempotency-Key": "folder-browser-secondary" }, data: { title: "第二画布", source_key: "folder-browser-secondary" },
    }));
    const secondPath = `${origin}/api/v1/projects/${project.id}/canvases/${secondary.id}`;
    page = await library(owner);
    report.step = "create and rename through source UI";
    const createdResponse = page.waitForResponse(response => response.request().method() === "PUT" && response.url().startsWith(folderPath + "/"));
    await page.getByRole("button", { name: "新建文件夹", exact: true }).click();
    const folder = (await read(await createdResponse)).folder;
    await page.getByRole("button", { name: "未命名文件夹 文件夹操作", exact: true }).click();
    await page.getByRole("menuitem", { name: "重命名", exact: true }).click();
    await page.getByPlaceholder("例如：短片项目", { exact: true }).fill("迁移验收文件夹");
    await page.getByRole("button", { name: /^保\s*存$/ }).click();
    await page.getByRole("button", { name: "迁移验收文件夹 文件夹操作", exact: true }).waitFor();
    await eventually(async () => (await read(await owner.request.get(folderPath))).folders, folders => folders[0]?.name === "迁移验收文件夹");
    report.original_create_and_rename = true;

    report.step = "cover selection and persistent bytes";
    await page.getByRole("button", { name: "迁移验收文件夹 文件夹操作", exact: true }).click();
    const choose = page.waitForEvent("filechooser");
    await page.getByRole("menuitem", { name: "更换封面", exact: true }).click();
    await (await choose).setFiles({ name: "folder-cover.png", mimeType: "image/png", buffer: Buffer.from(config.cover, "base64") });
    const folders = await eventually(async () => (await read(await owner.request.get(folderPath))).folders, folders => Boolean(folders[0]?.coverResourceId));
    const coverId = folders[0].coverResourceId;
    const coverBytes = await (await owner.request.get(`${origin}/api/v1/canvas-runtime/resources/${coverId}/file`)).body();
    assert.ok(coverBytes.length > 0);
    report.original_cover_saved = true;

    report.step = "move every canvas belonging to the source project";
    const title = initial.source_document.title;
    await move(page, title, "迁移验收文件夹");
    for (const canvasPath of [path, secondPath]) {
        await eventually(async () => (await read(await owner.request.get(`${canvasPath}/my-document`))).source_document, doc => doc.folderId === folder.id);
    }
    await owner.close();
    const freshOwner = await login(config.username);
    page = await library(freshOwner);
    await page.getByRole("button", { name: "打开文件夹 迁移验收文件夹", exact: true }).click();
    await page.getByRole("checkbox", { name: `选择 ${title}`, exact: true }).waitFor();
    assert.match(await page.locator("h1").textContent(), /迁移验收文件夹/);
    report.fresh_context_preserves_both_canvas_assignments = true;
    report.step = "move project out of folder";
    await move(page, title, "未分类");
    for (const canvasPath of [path, secondPath]) {
        await eventually(async () => (await read(await freshOwner.request.get(`${canvasPath}/my-document`))).source_document, doc => !doc.folderId);
    }
    await page.getByRole("button", { name: "全部项目 /", exact: true }).click();
    report.step = "move project back into folder";
    await move(page, title, "迁移验收文件夹");
    await page.getByRole("button", { name: "打开文件夹 迁移验收文件夹", exact: true }).click();
    for (const canvasPath of [path, secondPath]) {
        await eventually(async () => (await read(await freshOwner.request.get(`${canvasPath}/my-document`))).source_document, doc => doc.folderId === folder.id);
    }
    report.original_move_out_and_back = true;

    report.step = "export folder archive through source selection";
    await page.getByRole("checkbox", { name: `选择 ${title}`, exact: true }).check({ force: true });
    const download = page.waitForEvent("download");
    await page.getByRole("button", { name: "导出", exact: true }).click();
    const archivePath = resolve(output, "folder-project.zip");
    await (await download).saveAs(archivePath);
    const files = unzipSync(await readFile(archivePath));
    const manifest = JSON.parse(strFromU8(files["projects.json"]));
    assert.equal(manifest.folders.length, 1);
    assert.equal(manifest.projects.length, 2);
    assert.equal(manifest.folders[0].id, folder.id);
    assert.deepEqual(Buffer.from(files[manifest.folders[0].coverPath]), coverBytes);
    assert.ok(manifest.projects.every(item => item.project.folderId === folder.id));
    report.original_zip_includes_folder_cover_and_two_canvases = true;
    await page.screenshot({ path: resolve(output, "folder-selected.png") });

    report.step = "import folder archive in account with no source access";
    const importer = await login(config.importer);
    assert.equal((await importer.request.get(`${path}/my-document`)).status(), 404);
    page = await library(importer);
    await page.locator('input[type="file"][accept="application/zip,.zip"]').setInputFiles(archivePath);
    await page.getByText("已导入 2 个画布", { exact: true }).waitFor({ state: "attached", timeout: 60000 });
    const importedFolder = (await read(await importer.request.get(folderPath))).folders[0];
    assert.ok(importedFolder && importedFolder.id !== folder.id && importedFolder.coverResourceId !== coverId);
    const imported = (await read(await importer.request.get(`${origin}/api/v1/canvas-workspace?include_documents=true`))).items;
    assert.equal(imported.length, 2);
    assert.equal(new Set(imported.map(item => item.project_id)).size, 1);
    assert.notEqual(imported[0].project_id, project.id);
    assert.ok(imported.every(item => item.folder_id === importedFolder.id && item.source_document.folderId === importedFolder.id));
    const importedBytes = await (await importer.request.get(`${origin}/api/v1/canvas-runtime/resources/${importedFolder.coverResourceId}/file`)).body();
    assert.deepEqual(importedBytes, coverBytes);
    assert.equal((await freshOwner.request.get(`${origin}/api/v1/canvas-runtime/resources/${importedFolder.coverResourceId}/file`)).status(), 404);
    report.cross_account_zip_restored_private_folder_and_cover = true;

    report.step = "source folder delete moves contained project to recycle bin";
    page = await library(freshOwner);
    await page.getByRole("button", { name: "迁移验收文件夹 文件夹操作", exact: true }).click();
    await page.getByRole("menuitem", { name: "删除文件夹", exact: true }).click();
    await eventually(async () => (await read(await freshOwner.request.get(folderPath))).folders, folders => !folders.length);
    for (const canvasPath of [path, secondPath]) assert.equal((await freshOwner.request.get(`${canvasPath}/my-document`)).status(), 404);
    const remaining = (await read(await importer.request.get(`${origin}/api/v1/canvas-workspace`))).items;
    assert.equal(remaining.length, 2);
    assert.equal((await read(await importer.request.get(folderPath))).folders[0].id, importedFolder.id);
    report.original_delete_archives_source_without_affecting_import = true;
    assert.deepEqual(report.page_errors, []);
    report.step = "complete";
    process.stdout.write(JSON.stringify(report));
} catch (error) {
    if (page) await page.screenshot({ path: resolve(output, "failure.png") }).catch(() => undefined);
    process.stderr.write(JSON.stringify(report) + "\n" + (error.stack || String(error)));
    process.exitCode = 1;
} finally {
    await browser?.close();
    await server.close();
}
