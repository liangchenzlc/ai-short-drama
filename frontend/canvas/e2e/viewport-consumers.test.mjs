import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { chromium } from "playwright";
import { createServer } from "vite";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
// 原React控件和浏览器CSS几何，未调用供应商；完整保存路径由host-canvas另验。
test("实时倍率保持标题、重命名、选区和新增裁切／蒙版／标注控件，卸载释放观察器", { timeout: 120000 }, async () => {
    const media = resolve(process.env.CANVAS_PERF_MEDIA_DIR || resolve(root, ".runtime/r22-containment-root-v1/baseline-v2/media"), "sample.mp4");
    const server = await createServer({ root, cacheDir: resolve(root, ".runtime/vite-e2e-viewport-consumers"),
        server: { host: "127.0.0.1", port: 4193, strictPort: true }, logLevel: "error",
        plugins: [{ name: "viewport-consumers-fixture", configureServer(server) {
            server.middlewares.use(async (request, response, next) => {
                try {
                    if (request.url === "/__viewport-sample.mp4") { response.setHeader("Content-Type", "video/mp4"); response.end(await readFile(media)); return; }
                    if (!request.url?.includes("/__viewport-consumers")) return next();
                    response.setHeader("Content-Type", "text/html; charset=utf-8");
                    response.end(await server.transformIndexHtml(request.url, '<!doctype html><html><head><link rel="icon" href="data:,"></head><body><div id="root"></div><script type="module" src="/e2e/fixtures/viewport-consumers.tsx"></script></body></html>'));
                } catch (error) { next(error); }
            });
        } }],
    });
    await server.listen(); let browser;
    try {
        browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH });
        const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, reducedMotion: "reduce" });
        const page = await context.newPage(), errors = [], requests = [];
        page.on("pageerror", error => errors.push(error.message));
        page.on("request", request => { if (new URL(request.url()).pathname.startsWith("/api/")) requests.push(request.url()); });
        await page.addInitScript(() => {
            const Native = window.MutationObserver; window.__viewportObservers = new Set();
            window.MutationObserver = class extends Native {
                observe(target, options) { if (target.hasAttribute?.("data-canvas-viewport")) window.__viewportObservers.add(this); return super.observe(target, options); }
                disconnect() { window.__viewportObservers.delete(this); return super.disconnect(); }
            };
        });
        await page.goto("http://127.0.0.1:4193/canvas-app/__viewport-consumers");
        const header = page.locator(".canvas-node-external-header"); await header.waitFor();
        await page.waitForFunction(() => window.__viewportObservers.size === 1);
        const zoom = async () => {
            await page.mouse.move(1000, 700); await page.keyboard.down("Control"); await page.mouse.wheel(0, -24); await page.keyboard.up("Control");
            await page.waitForFunction(() => Number(document.querySelector("[data-canvas-viewport]").style.getPropertyValue("--canvas-live-scale")) > 1.05);
            await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
        };
        const before = await header.boundingBox(); await zoom(); const after = await header.boundingBox();
        assert.ok(Math.abs(after.height - before.height) < .25, "原标题在实时缩放时保持屏幕高度");
        await page.getByRole("button", { name: "编辑节点名称：原节点标题", exact: true }).click();
        const name = page.getByRole("textbox", { name: "节点名称", exact: true }); await name.fill("缩放后重命名"); await name.press("Enter");
        assert.equal(await page.getByLabel("已改标题").textContent(), "缩放后重命名");
        await page.getByRole("button", { name: "预览选区", exact: true }).click();
        const outline = await page.locator('[data-node-id="image"]').evaluate(element => ({ width: parseFloat(getComputedStyle(element).outlineWidth), scale: Number(document.querySelector("[data-canvas-viewport]").style.getPropertyValue("--canvas-live-scale")) }));
        assert.ok(Math.abs(outline.width * outline.scale - 2) < 1, "首次框选必须使用当前倍率");
        await page.getByRole("button", { name: "清除选区", exact: true }).click();
        await page.getByRole("button", { name: "切换节点", exact: true }).click();
        assert.equal(await header.count(), 0);
        await page.getByRole("button", { name: "切换节点", exact: true }).click();
        await header.waitFor();
        assert.ok(Math.abs((await header.boundingBox()).height - before.height) < .25, "新增节点标题不能使用旧倍率");
        for (const [tool, selector] of [["crop", "[data-image-crop-inline]"], ["mask", "[data-image-mask-edit-inline]"], ["annotation", "[data-image-annotation-inline]"], ["video", "[data-video-crop-inline]"]]) {
            await page.waitForFunction(() => !document.querySelector("[data-canvas-viewport]").dataset.canvasViewportInteracting);
            await page.getByRole("button", { name: "复位", exact: true }).click();
            await page.waitForFunction(() => !document.querySelector("[data-canvas-viewport]").dataset.canvasViewportInteracting && Number(document.querySelector("[data-canvas-viewport]").style.getPropertyValue("--canvas-live-scale")) === 1);
            await zoom();
            await page.getByRole("button", { name: tool, exact: true }).click();
            const toolbar = page.locator(`${selector} > [data-canvas-no-zoom]`).first(); await toolbar.waitFor();
            const mounted = await toolbar.boundingBox();
            const geometry = await toolbar.evaluate(element => ({ cssHeight: getComputedStyle(element).height, transform: getComputedStyle(element).transform, inline: element.style.cssText, viewport: document.querySelector("[data-canvas-viewport]").style.cssText, world: document.querySelector("[data-canvas-world-layer]").style.cssText, raster: document.querySelector("[data-canvas-world-raster-layer]").style.cssText }));
            assert.ok(Math.abs(mounted.height - (tool === "crop" || tool === "video" ? 44 : 56)) < .25, `新增原工具栏保持屏幕高度：${JSON.stringify({tool, mounted, geometry})}`);
            const k = await page.locator("[data-canvas-viewport]").evaluate(element => Number(element.style.getPropertyValue("--canvas-live-scale")));
            await page.mouse.move(1000, 700); await page.keyboard.down("Control"); await page.mouse.wheel(0, -24); await page.keyboard.up("Control");
            await page.waitForFunction(k => Number(document.querySelector("[data-canvas-viewport]").style.getPropertyValue("--canvas-live-scale")) > k + .01, k);
            await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
            const scaled = await toolbar.boundingBox();
            for (const dimension of ["width", "height"]) assert.ok(Math.abs(scaled[dimension] - mounted[dimension]) < .5, `${tool}工具栏在真实缩放时保持屏幕${dimension}`);
            if (tool === "crop" || tool === "video") assert.ok(Math.abs((await page.locator(selector).getByRole("button", { name: "resize-nw", exact: true }).boundingBox()).width - 20) < .25);
        }
        await page.getByRole("button", { name: "切换画布", exact: true }).click();
        await page.waitForFunction(() => window.__viewportObservers.size === 0);
        await page.getByRole("button", { name: "切换画布", exact: true }).click();
        await page.waitForFunction(() => window.__viewportObservers.size === 1);
        assert.deepEqual(errors, []); assert.deepEqual(requests, []);
        await context.close();
    } finally { await browser?.close(); await server.close(); }
});
