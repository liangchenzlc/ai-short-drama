import assert from "node:assert/strict";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { chromium } from "playwright";
import { createServer } from "vite";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
test("Markdown按需加载保留最新流式正文、表格、空态和加载失败反馈", { timeout: 90000 }, async () => {
    const server = await createServer({
        root, cacheDir: resolve(root, ".runtime/vite-e2e-markdown-lazy"),
        server: { host: "127.0.0.1", port: 4191, strictPort: true }, logLevel: "error",
        plugins: [{ name: "markdown-lazy-fixture", configureServer(server) {
            server.middlewares.use(async (request, response, next) => {
                if (!request.url?.includes("/__markdown-lazy")) return next();
                response.setHeader("Content-Type", "text/html; charset=utf-8");
                try { response.end(await server.transformIndexHtml(request.url, '<!doctype html><html><head><link rel="icon" href="data:,"></head><body><div id="root"></div><script type="module" src="/e2e/fixtures/markdown-lazy.tsx"></script></body></html>')); }
                catch (error) { next(error); }
            });
        } }],
    });
    await server.listen();
    let browser;
    try {
        browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH });
        const context = await browser.newContext();
        const page = await context.newPage();
        const errors = [], imports = [];
        page.on("pageerror", error => errors.push(error.message));
        let release;
        const held = new Promise(accept => { release = accept; });
        const renderer = /\/src\/components\/ai\/ai-message-markdown-renderer\.tsx(?:\?|$)/;
        await page.route(renderer, async route => { imports.push(route.request().url()); await held; await route.continue(); });
        await page.goto("http://127.0.0.1:4191/canvas-app/__markdown-lazy");
        await page.getByRole("button", { name: "显示内容", exact: true }).waitFor();
        assert.deepEqual(imports, [], "空正文不能下载重型渲染器");
        const region = page.getByRole("region", { name: "Markdown内容", exact: true });
        await page.getByRole("button", { name: "显示内容", exact: true }).click();
        await region.getByRole("status").waitFor();
        await page.getByRole("button", { name: "更新内容", exact: true }).click();
        release();
        await region.getByRole("heading", { name: "更新标题", exact: true }).waitFor();
        assert.equal(imports.length, 1);
        assert.equal(await region.getByRole("heading", { name: "初始标题", exact: true }).count(), 0);
        const emphasis = region.getByText("更新正文", { exact: true }).first();
        await emphasis.waitFor();
        assert.ok(Number(await emphasis.evaluate(element => getComputedStyle(element).fontWeight)) >= 600);
        assert.equal(await region.getByRole("cell", { name: "3", exact: true }).count(), 1);
        await page.getByRole("button", { name: "完成输出", exact: true }).click();
        await page.getByRole("button", { name: "隐藏内容", exact: true }).click();
        assert.equal(await region.innerText(), "");
        assert.deepEqual(errors, []);
        await context.close();
        const failedContext = await browser.newContext();
        const failedPage = await failedContext.newPage();
        await failedPage.route(renderer, async route => { await route.fulfill({ status: 503, contentType: "application/javascript", body: "" }); });
        failedPage.on("pageerror", error => errors.push(error.message));
        await failedPage.goto("http://127.0.0.1:4191/canvas-app/__markdown-lazy");
        await failedPage.getByRole("button", { name: "显示内容", exact: true }).click();
        await failedPage.getByRole("alert").waitFor();
        await failedPage.unroute(renderer);
        assert.match(await failedPage.getByRole("alert").innerText(), /刷新页面后重试/);
        await failedPage.reload();
        await failedPage.getByRole("button", { name: "显示内容", exact: true }).click();
        await failedPage.getByRole("heading", { name: "初始标题", exact: true }).waitFor();
        assert.deepEqual(errors, []);
        await failedContext.close();
    } finally {
        await browser?.close();
        await server.close();
    }
});
