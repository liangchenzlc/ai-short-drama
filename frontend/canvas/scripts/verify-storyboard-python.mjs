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
const report = { step: "start", width: config.width, page_errors: [], http_errors: [], task_posts: 0, resource_posts: 0, creation_run_posts: 0 };
await mkdir(output, { recursive: true });
const server = await createServer({ cacheDir: resolve(".runtime/vite-storyboard-browser"), server: { host: "127.0.0.1", port: 4199, strictPort: true, proxy: { "/api": { target: config.apiUrl, changeOrigin: true } } }, logLevel: "silent" });
await server.listen();
let browser;
let page;
let owner;
const script = () => page.locator('[data-node-id="existing-script"]');
const dialog = () => page.getByRole("dialog", { name: "分镜脚本", exact: true });
const tableRows = () => dialog().locator("tbody tr[data-row-key]");

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
    assert.fail(`分镜状态未达到预期：${JSON.stringify(value)}`);
}
async function graph(context = owner, predicate = () => true) {
    return eventually(async () => (await read(await context.request.get(`${origin}${path}/my-document`))).source_document, predicate);
}
async function rows(predicate = () => true) {
    const document = await graph(owner, doc => predicate(doc.nodes.find(node => node.id === "existing-script").metadata.storyboard.rows));
    return document.nodes.find(node => node.id === "existing-script").metadata.storyboard.rows;
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
        if (request.method() !== "POST") return;
        if (route === "/api/v1/canvas-runtime/tasks") report.task_posts++;
        if (route === "/api/v1/canvas-runtime/resources") report.resource_posts++;
        if (route === "/api/v1/canvas-runtime/creation-runs") report.creation_run_posts++;
    });
    page.on("response", response => {
        const route = new URL(response.url()).pathname;
        if (response.status() >= 400 && route.startsWith("/api/")) report.http_errors.push({ path: route, status: response.status() });
    });
    await page.goto(`${origin}/canvas-app/canvas/${config.sourceKey}`);
    await page.locator("[data-canvas-viewport]").waitFor({ state: "visible" });
    await page.evaluate(() => document.fonts.ready);
    await script().getByRole("region", { name: "分镜镜头列表", exact: true }).waitFor({ state: "visible" });
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
async function save() {
    await page.getByRole("button", { name: /^画布保存状态：/ }).press("Control+s");
    await page.locator(".ant-message").getByText(/^画布已保存(?:到云端)?$/, { exact: true }).waitFor({ state: "attached" });
    await page.getByRole("button", { name: "画布保存状态：已保存", exact: true }).waitFor({ state: "visible" });
}
async function closeEditor() {
    await dialog().getByRole("button", { name: "Close", exact: true }).click();
    await dialog().waitFor({ state: "hidden" });
}
async function openEditor() {
    await script().getByRole("button", { name: "全屏编辑", exact: true }).click();
    await dialog().waitFor({ state: "visible" });
}
async function captureEditor(name) {
    await page.waitForFunction(async () => {
        const editor = Array.from(document.querySelectorAll('.ant-modal[role="dialog"]')).find(element => getComputedStyle(element).display !== "none");
        if (!editor) return false;
        let previous;
        let stable = 0;
        while (stable < 15) {
            await new Promise(requestAnimationFrame);
            const rect = editor.getBoundingClientRect();
            const style = getComputedStyle(editor);
            const state = JSON.stringify([rect.x, rect.y, rect.width, rect.height, style.transform, style.opacity]);
            const active = editor.getAnimations({ subtree: true }).some(animation => animation.effect?.getTiming().iterations !== Infinity && (animation.playState === "running" || animation.pending));
            stable = state === previous && !active ? stable + 1 : 0;
            previous = state;
        }
        return true;
    });
    await page.screenshot({ path: resolve(output, name) });
}
async function chooseSetting(index, option) {
    await script().locator(".ant-select-selector").nth(index).click();
    await page.locator(".ant-select-dropdown:visible").getByText(option, { exact: true }).click();
}
async function checkRows(expected) {
    await openEditor();
    assert.equal(await tableRows().count(), expected.length);
    assert.deepEqual(await tableRows().evaluateAll(elements => elements.map(element => element.getAttribute("data-row-key"))), expected.map(row => row.id));
    for (const row of expected) {
        const line = dialog().locator(`tr[data-row-key="${row.id}"]`);
        assert.equal(await line.getByPlaceholder("填写画面描述", { exact: true }).inputValue(), row.plotDescription);
        assert.equal(await line.getByPlaceholder("填写视频提示词", { exact: true }).inputValue(), row.videoMotionPrompt);
        assert.equal(await line.getByPlaceholder("填写台词/旁白", { exact: true }).inputValue(), row.dialogue);
    }
}
function mediaAssociations(document) {
    const source = document.nodes.find(node => node.id === "existing-script");
    const result = source.metadata.storyboard.rows.flatMap(row => [
        { rowId: row.id, shotNumber: row.shotNumber, nodeId: row.imageNodeId, type: "image", prompt: row.imageGenerationPrompt || row.plotDescription },
        { rowId: row.id, shotNumber: row.shotNumber, nodeId: row.videoNodeId, type: "video", prompt: row.videoMotionPrompt || row.plotDescription },
    ]);
    assert.equal(document.nodes.length, 7);
    assert.equal(document.connections.length, 6);
    assert.equal(new Set(result.map(item => item.nodeId)).size, 6);
    for (const item of result) {
        const node = document.nodes.find(node => node.id === item.nodeId);
        assert.equal(node?.type, item.type);
        assert.equal(node.metadata.workflowKind, "shot");
        assert.equal(node.metadata.shotIndex, item.shotNumber);
        assert.equal(node.metadata.prompt, item.prompt);
        assert.equal(node.metadata.composerContent, item.prompt);
        assert.ok(!node.metadata.content && !node.metadata.taskId);
        const connections = document.connections.filter(connection => connection.toNodeId === node.id);
        assert.equal(connections.length, 1);
        assert.equal(connections[0].fromNodeId, source.id);
        assert.equal(connections[0].fromHandleId, `row:${item.rowId}`);
        assert.equal(connections[0].storyboardRowId, item.rowId);
        assert.equal(connections[0].relation, "storyboard-output");
    }
    return result;
}

try {
    browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH });
    owner = await context(config.username);
    const member = await context(config.memberUsername);
    await open(owner);
    await fit();
    report.step = "源脚本创建菜单保持正在开发";
    await script().getByRole("button", { name: /添加第一个镜头/ }).waitFor({ state: "visible" });
    await page.locator("[data-canvas-viewport]").click({ button: "right", position: { x: 25, y: 150 } });
    await page.locator("[data-canvas-context-menu]").getByRole("button", { name: "添加节点", exact: true }).click();
    assert.ok(await page.getByTitle("脚本：正在开发", { exact: true }).isDisabled());
    await page.getByRole("button", { name: "搜索节点", exact: true }).click();
    await page.getByRole("textbox", { name: "搜索节点", exact: true }).fill("脚本");
    assert.ok(await page.getByTitle("脚本：正在开发", { exact: true }).isDisabled());
    await page.keyboard.press("Escape");
    await page.keyboard.press("Escape");
    assert.equal((await graph()).nodes.length, 1);
    report.creation_menu_disabled_like_source = true;

    report.step = "原表添加与就地编辑";
    await script().getByRole("button", { name: /添加第一个镜头/ }).click();
    const first = (await rows(value => value.length === 1))[0];
    assert.equal(first.durationSeconds, 6);
    report.empty_state_and_first_row = true;
    await script().getByRole("button", { name: "镜头 1 操作", exact: true }).click();
    assert.equal(await page.getByRole("menuitem", { name: "删除镜头", exact: true }).getAttribute("aria-disabled"), "true");
    await page.keyboard.press("Escape");
    report.last_inline_row_delete_disabled = true;
    const duration = script().getByRole("spinbutton", { name: "镜头时长（秒）", exact: true }).first();
    await duration.fill("0");
    await rows(value => value[0].durationSeconds === 1);
    await duration.fill("61");
    await rows(value => value[0].durationSeconds === 60);
    await duration.fill("5");
    await rows(value => value[0].durationSeconds === 5);
    report.inline_duration_bounds = true;
    await script().getByPlaceholder("描述视频运动、镜头和动作", { exact: true }).first().fill("摄影机跟随角色走进车站，停在检票口。");
    await script().getByPlaceholder("台词或旁白", { exact: true }).first().fill("我回来了。\n列车缓缓停下。");
    await rows(value => value[0].dialogue === "我回来了。\n列车缓缓停下。");
    await script().getByRole("button", { name: "添加行", exact: true }).click();
    const deleted = (await rows(value => value.length === 2))[1].id;
    await script().getByRole("button", { name: "镜头 2 操作", exact: true }).click();
    await page.getByRole("menuitem", { name: "删除镜头", exact: true }).click();
    await rows(value => value.length === 1 && value[0].id === first.id);
    await script().getByRole("button", { name: "添加行", exact: true }).click();
    const second = (await rows(value => value.length === 2))[1];
    assert.notEqual(second.id, deleted);
    await script().getByPlaceholder("描述视频运动、镜头和动作", { exact: true }).nth(1).fill("角色踏上台阶，摄影机保持中景。");
    await script().getByPlaceholder("台词或旁白", { exact: true }).nth(1).fill("下一站见。");
    await rows(value => value[1].dialogue === "下一站见。");
    report.inline_edit_and_delete = true;

    report.step = "原键盘提交在缺画风时阻止生成";
    report.private_prompt = "角色回到车站，与朋友道别。保留人物情绪变化。";
    const composer = script().getByRole("textbox", { name: "分镜剧情与项目设定", exact: true });
    await composer.fill(report.private_prompt);
    await composer.press("Control+Enter");
    await page.locator(".ant-message").getByText("请先设置项目画风，再生成分镜", { exact: true }).waitFor({ state: "attached" });
    assert.equal(report.task_posts, 0);
    assert.equal(report.creation_run_posts, 0);
    report.missing_style_blocks_keyboard_generation = true;
    await chooseSetting(0, "3 镜");
    await chooseSetting(1, "每镜 10 秒");
    await graph(owner, doc => doc.nodes[0].metadata.storyboardShotCount === "3" && doc.nodes[0].metadata.storyboardShotDuration === "10");
    await page.screenshot({ path: resolve(output, "storyboard-inline.png") });

    report.step = "原全屏列选择、复制、排序与搜索";
    await openEditor();
    report.original_fullscreen_editor = true;
    await dialog().getByRole("checkbox", { name: "画面描述", exact: true }).press("Space");
    assert.ok(await dialog().getByRole("checkbox", { name: "画面描述", exact: true }).isChecked());
    await dialog().getByRole("checkbox", { name: "镜头设计", exact: true }).press("Space");
    assert.ok(await dialog().getByRole("checkbox", { name: "镜头设计", exact: true }).isChecked());
    await graph(owner, doc => doc.nodes[0].metadata.storyboard.visibleColumns.includes("camera"));
    report.column_visibility_saved = true;
    const firstLine = dialog().locator(`tr[data-row-key="${first.id}"]`);
    await firstLine.getByPlaceholder("填写画面描述", { exact: true }).fill("角色走进车站，停在检票口。");
    await firstLine.getByPlaceholder("填写镜头设计", { exact: true }).fill("中景，平视，固定机位。");
    await rows(value => value[0].camera === "中景，平视，固定机位。");
    await firstLine.getByTitle("复制", { exact: true }).click();
    const copiedRows = await rows(value => value.length === 3);
    const duplicate = copiedRows[1];
    assert.notEqual(duplicate.id, first.id);
    assert.equal(duplicate.imageNodeId, undefined);
    assert.equal(duplicate.videoNodeId, undefined);
    assert.equal(duplicate.status, "idle");
    assert.equal(duplicate.dialogue, copiedRows[0].dialogue);
    const secondLine = dialog().locator(`tr[data-row-key="${second.id}"]`);
    await secondLine.getByPlaceholder("填写画面描述", { exact: true }).fill("角色独自踏上台阶。");
    await secondLine.getByTitle("上移", { exact: true }).click();
    await rows(value => value.map(row => row.id).join(",") === [first.id, second.id, duplicate.id].join(","));
    await dialog().locator(`tr[data-row-key="${duplicate.id}"]`).getByTitle("删除", { exact: true }).click();
    await rows(value => value.length === 2 && value[1].id === second.id);
    report.duplicate_and_reorder_keep_ids = true;
    const search = dialog().getByPlaceholder("筛选画面、台词或提示词", { exact: true });
    await search.fill("台阶");
    await eventually(() => tableRows().evaluateAll(elements => elements.map(element => element.getAttribute("data-row-key"))), value => value.length === 1 && value[0] === second.id);
    await search.fill("没有这个镜头");
    await dialog().locator(".ant-empty-description").getByText("暂无数据", { exact: true }).waitFor({ state: "visible" });
    assert.equal(await tableRows().count(), 0);
    await search.fill("");
    await eventually(() => tableRows().count(), value => value === 2);
    report.search_filters_rows = true;
    await dialog().getByRole("button", { name: "新增镜头", exact: true }).click();
    const third = (await rows(value => value.length === 3))[2];
    const thirdLine = dialog().locator(`tr[data-row-key="${third.id}"]`);
    await thirdLine.getByPlaceholder("填写画面描述", { exact: true }).fill("车站灯光亮起，列车远去。");
    await thirdLine.getByPlaceholder("填写视频提示词", { exact: true }).fill("列车驶离，摄影机停留在空站台。");
    await thirdLine.getByPlaceholder("填写台词/旁白", { exact: true }).fill("旅程继续。");
    await rows(value => value[2].dialogue === "旅程继续。");
    assert.ok(await dialog().getByRole("button", { name: "生成分镜图", exact: true }).isDisabled());
    assert.ok(await dialog().getByRole("button", { name: "生成视频", exact: true }).isDisabled());
    await dialog().getByText("先做首帧", { exact: true }).click();
    await graph(owner, doc => doc.nodes[0].metadata.storyboardVideoInputMode === "keyframe");
    assert.ok(await dialog().getByRole("button", { name: "生成首帧", exact: true }).isDisabled());
    assert.ok(await dialog().getByRole("button", { name: "确认首帧并生成", exact: true }).isDisabled());
    report.direct_and_keyframe_settings = true;
    await captureEditor("storyboard-fullscreen.png");
    await closeEditor();

    report.step = "原更多菜单创建镜头图片与视频节点";
    await script().getByRole("button", { name: "更多操作", exact: true }).press("Enter");
    await page.getByRole("menuitem", { name: "创建 3 个图片节点", exact: true }).click();
    await graph(owner, doc => doc.nodes.length === 4 && doc.connections.length === 3);
    await script().getByRole("button", { name: "更多操作", exact: true }).press("Enter");
    assert.equal(await page.getByRole("menuitem", { name: "图片节点已创建", exact: true }).getAttribute("aria-disabled"), "true");
    await page.getByRole("menuitem", { name: "创建 3 个视频节点", exact: true }).click();
    const associated = await graph(owner, doc => doc.nodes.length === 7 && doc.connections.length === 6);
    report.media_associations = mediaAssociations(associated);
    report.original_media_nodes_and_row_connections = true;
    await script().getByRole("button", { name: "更多操作", exact: true }).press("Enter");
    for (const name of ["图片节点已创建", "视频节点已创建"]) assert.equal(await page.getByRole("menuitem", { name, exact: true }).getAttribute("aria-disabled"), "true");
    await page.keyboard.press("Escape");
    assert.deepEqual(mediaAssociations(await graph()), report.media_associations);
    report.media_creation_not_repeated = true;
    await fit();
    await page.screenshot({ path: resolve(output, "storyboard-associated-nodes.png") });
    await save();
    const final = await graph();
    const finalScript = final.nodes.find(node => node.id === "existing-script");
    report.final_rows = finalScript.metadata.storyboard.rows;
    report.final_columns = finalScript.metadata.storyboard.visibleColumns;

    report.step = "仅cookies恢复稳定镜头与私人设置";
    const cookies = await owner.cookies();
    await owner.close();
    page = undefined;
    owner = await context(undefined, cookies);
    assert.deepEqual((await owner.storageState()).origins, []);
    await open(owner);
    await fit();
    assert.deepEqual(mediaAssociations(await graph()), report.media_associations);
    assert.equal(await script().getByRole("textbox", { name: "分镜剧情与项目设定", exact: true }).inputValue(), report.private_prompt);
    await checkRows(report.final_rows);
    assert.ok(await dialog().getByRole("button", { name: "确认首帧并生成", exact: true }).isVisible());
    await captureEditor("storyboard-restored.png");
    await closeEditor();
    report.cookie_only_recovery = true;
    await owner.close();
    page = undefined;


    assert.deepEqual(report.page_errors, []);
    assert.deepEqual(report.http_errors, []);
    assert.equal(report.task_posts, 0);
    assert.equal(report.resource_posts, 0);
    assert.equal(report.creation_run_posts, 0);
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
