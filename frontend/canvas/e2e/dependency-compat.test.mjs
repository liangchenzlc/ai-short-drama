import assert from "node:assert/strict";
import { mkdir, readFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";
import { createServer } from "vite";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");

test("宿主 AntD5 下原提示词助手保留长对话滚动、Sender 发送及页脚操作", { timeout: 90000 }, async () => {
    const server = await createServer({
        root,
        server: { host: "127.0.0.1", port: 4188, strictPort: true },
        logLevel: "error",
        plugins: [{
            name: "canvas-dependency-compat-fixture",
            configureServer(server) {
                server.middlewares.use(async (request, response, next) => {
                    if (!request.url?.includes("/__dependency-compat")) return next();
                    response.setHeader("Content-Type", "text/html; charset=utf-8");
                    const html = '<!doctype html><html><head><link rel="icon" href="data:,"></head><body><div id="root"></div><script type="module" src="/e2e/fixtures/dependency-compat.tsx"></script></body></html>';
                    try { response.end(await server.transformIndexHtml(request.url, html)); }
                    catch (error) { next(error); }
                });
            },
        }],
    });
    await server.listen();
    let browser;
    let page;
    const errors = [];
    try {
        browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH });
        page = await browser.newPage({ viewport: { width: 1440, height: 900 }, reducedMotion: "reduce" });
        page.on("pageerror", (error) => errors.push(error.message));
        page.on("console", (message) => { if (message.type() === "error") errors.push(message.text()); });
        page.on("requestfailed", (request) => errors.push(`${request.method()} ${new URL(request.url()).pathname}: ${request.failure()?.errorText}`));
        await page.goto("http://127.0.0.1:4188/canvas-app/__dependency-compat");
        const sender = page.locator(".canvas-prompt-optimizer-sender textarea");
        await sender.waitFor({ state: "visible", timeout: 60000 });
        await page.evaluate(() => document.fonts.ready);
        assert.equal(await sender.inputValue(), "初始镜头");
        assert.equal(await page.locator(".canvas-prompt-optimizer-sender .ant-sender-actions-list button").count(), 0);
        await page.getByRole("button", { name: "选择优化方式", exact: true }).click();
        await page.getByRole("menuitem").filter({ hasText: "精修已有" }).click();
        await sender.fill("新的镜头要求");
        await sender.press("Enter");
        await page.getByRole("textbox", { name: "优化后的提示词", exact: true }).waitFor({ state: "visible" });
        await page.waitForFunction(() => {
            const scroll = document.querySelector(".canvas-prompt-optimizer-chat .ant-bubble-list-scroll-box");
            return scroll && scroll.scrollHeight > scroll.clientHeight && Math.abs(scroll.scrollHeight - scroll.clientHeight - scroll.scrollTop) < 3;
        });
        const first = await page.evaluate(() => window.__dependencyCompat.inputs);
        assert.equal(first.length, 1);
        assert.equal(first[0].prompt, "新的镜头要求");
        assert.equal(first[0].mode, "refine");
        assert.equal(await sender.inputValue(), "");
        assert.ok(await page.getByRole("button", { name: "发送优化请求", exact: true }).isVisible());
        await sender.fill("第二次镜头要求");
        await page.getByRole("button", { name: "发送优化请求", exact: true }).click();
        await page.waitForFunction(() => window.__dependencyCompat.inputs.length === 2);
        await page.getByRole("textbox", { name: "优化后的提示词", exact: true }).waitFor({ state: "visible" });
        await page.getByRole("button", { name: "采用优化后的提示词", exact: true }).click();
        await page.waitForFunction(() => typeof window.__dependencyCompat.applied === "string");
        assert.match(await page.evaluate(() => window.__dependencyCompat.applied), /镜头细节29/);
        await page.getByRole("button", { name: "选择模型 Logo", exact: true }).click();
        const logoList = page.getByRole("listbox", { name: "模型 Logo 列表", exact: true });
        await logoList.waitFor({ state: "visible" });
        assert.equal(await logoList.getByRole("option").count(), 321);
        const toc = JSON.parse(await readFile(resolve(root, "vendor/lobehub-icons/es/toc.json"), "utf8"));
        const brand = toc.find((item) => item.id === "NanoBanana");
        await logoList.getByTitle(brand.fullTitle || brand.title, { exact: true }).click();
        await page.getByRole("button", { name: "选择模型 Logo", exact: true }).locator("svg title").filter({ hasText: /Nano/ }).waitFor({ state: "attached" });
        assert.deepEqual(errors, []);
    } catch (error) {
        if (page) {
            await mkdir(resolve(root, ".runtime/dependency-compat"), { recursive: true });
            await page.screenshot({ path: resolve(root, ".runtime/dependency-compat/failure.png"), fullPage: true }).catch(() => {});
        }
        throw new Error(`${error.stack || error}\n浏览器错误：${JSON.stringify(errors)}`, { cause: error });
    } finally {
        await browser?.close();
        await server.close();
    }
});
