import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { chromium } from "playwright";
import { createServer } from "vite";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
test("表情3D预览按需加载原GLB，保留键盘／手动框选和局部失败关闭", { timeout: 90000 }, async () => {
    const server = await createServer({ root, cacheDir: resolve(root, ".runtime/vite-e2e-emotion-lazy"),
        server: { host: "127.0.0.1", port: 4192, strictPort: true }, logLevel: "error",
        plugins: [{ name: "emotion-lazy-fixture", configureServer(server) {
            server.middlewares.use(async (request, response, next) => {
                if (!request.url?.includes("/__emotion-lazy")) return next();
                response.setHeader("Content-Type", "text/html; charset=utf-8");
                try { response.end(await server.transformIndexHtml(request.url, '<!doctype html><html><head><link rel="icon" href="data:,"></head><body><div id="root"></div><script type="module" src="/e2e/fixtures/emotion-lazy.tsx"></script></body></html>')); }
                catch (error) { next(error); }
            });
        } }],
    });
    await server.listen();
    let browser;
    try {
        browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH });
        const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, reducedMotion: "reduce" });
        const page = await context.newPage();
        const errors = [], imports = [], mutations = [];
        page.on("pageerror", error => errors.push(error.message));
        page.on("request", request => { if (new URL(request.url()).pathname.startsWith("/api/") && request.method() !== "GET") mutations.push(request.url()); });
        let release;
        const held = new Promise(accept => { release = accept; });
        const preview = /\/src\/components\/canvas\/canvas-emotion-head-preview\.tsx(?:\?|$)/;
        await page.route(preview, async route => { imports.push(route.request().url()); await held; await route.continue(); });
        await page.goto("http://127.0.0.1:4192/canvas-app/__emotion-lazy");
        await page.getByRole("button", { name: "打开表情", exact: true }).waitFor();
        assert.deepEqual(imports, []);
        await page.getByRole("button", { name: "打开表情", exact: true }).click();
        await page.getByRole("status").filter({ hasText: "正在加载预览" }).waitFor();
        const slider = page.getByRole("slider", { name: "情绪强度", exact: true });
        const before = await slider.getAttribute("aria-valuetext");
        await slider.press("ArrowRight");
        assert.notEqual(await slider.getAttribute("aria-valuetext"), before);
        await page.getByRole("button", { name: "手动框选", exact: true }).click();
        assert.equal(await page.getByLabel("手动框选次数").textContent(), "1");
        const model = page.waitForResponse(response => new URL(response.url()).pathname.endsWith("/canvas/models/facecap.glb"));
        release();
        const response = await model;
        assert.ok(response.ok());
        assert.ok((await response.body()).length > 1000);
        await page.waitForFunction(() => [...document.querySelectorAll("canvas")].some(canvas => canvas.width > 0 && canvas.height > 0));
        await page.getByText(/^实时预览 ·/).waitFor();
        assert.equal(imports.length, 1);
        await mkdir(resolve(root, ".runtime/emotion-lazy"), { recursive: true });
        await page.screenshot({ path: resolve(root, ".runtime/emotion-lazy/preview.png") });
        await page.getByRole("button", { name: "关闭情绪调节", exact: true }).click();
        await page.waitForFunction(() => document.querySelectorAll("canvas").length === 0);
        assert.deepEqual(errors, []); assert.deepEqual(mutations, []);
        await context.close();
        const failedContext = await browser.newContext();
        const failedPage = await failedContext.newPage();
        await failedPage.route(preview, route => route.fulfill({ status: 503, contentType: "application/javascript", body: "" }));
        await failedPage.goto("http://127.0.0.1:4192/canvas-app/__emotion-lazy");
        await failedPage.getByRole("button", { name: "打开表情", exact: true }).click();
        await failedPage.getByRole("alert").filter({ hasText: "预览加载失败" }).waitFor();
        await failedPage.getByRole("slider", { name: "情绪强度", exact: true }).press("ArrowDown");
        await failedPage.getByRole("button", { name: "关闭情绪调节", exact: true }).click();
        assert.equal(await failedPage.getByRole("alert").count(), 0);
        await failedContext.close();
    } finally { await browser?.close(); await server.close(); }
});
