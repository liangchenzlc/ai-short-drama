import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import test from "node:test";
import { chromium } from "playwright";
import { createServer } from "vite";

test("画布初始化及主题刷新继续使用同一品牌标题、描述和方形图标", async t => {
    const brandRoot = resolve("../../backend/src/short_drama/core/branding");
    const brand = JSON.parse(await readFile(resolve(brandRoot, "brand.json"), "utf8"));
    const server = await createServer({
        cacheDir: resolve(".runtime/vite-branding"),
        server: { host: "127.0.0.1", port: 0, strictPort: false, warmup: { clientFiles: [] } },
        plugins: [{
            name: "branding-verification-page",
            configureServer(vite) {
                vite.middlewares.use((request, response, next) => {
                    if (request.url !== "/canvas-app/brand-check.html") return next();
                    response.setHeader("Content-Type", "text/html");
                    response.end('<!doctype html><html><head><title>初始化之前</title></head><body></body></html>');
                });
            },
        }],
    });
    t.after(() => server.close());
    await server.listen();
    const browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH });
    t.after(() => browser.close());
    const page = await browser.newPage();
    const errors = [];
    page.on("pageerror", error => errors.push(error.message));
    await page.goto(`${server.resolvedUrls.local[0]}brand-check.html`);
    await page.evaluate(async () => {
        const { bootstrapAppearance } = await import("/canvas-app/src/services/appearance-bootstrap.ts");
        await bootstrapAppearance();
    });

    const expectedTitle = brand.titles.canvas.replaceAll("{name}", brand.name);
    const expectedDescription = brand.descriptions.canvas.replaceAll("{name}", brand.name);
    for (const theme of ["light", "dark"]) {
        await page.evaluate(async mode => {
            const { applyAppearanceMetadata, useAppearanceStore } = await import("/canvas-app/src/stores/use-appearance-store.ts");
            document.documentElement.classList.toggle("dark", mode === "dark");
            applyAppearanceMetadata(useAppearanceStore.getState().appearance);
        }, theme);
        assert.equal(await page.title(), expectedTitle);
        assert.equal(await page.locator('meta[name="description"]').getAttribute("content"), expectedDescription);
        assert.equal(await page.locator('meta[property="og:site_name"]').getAttribute("content"), brand.name);
        const iconUrl = await page.locator('link[rel="icon"]').getAttribute("href");
        assert.match(iconUrl, /^\/canvas-app\/static\/branding\/[^/?#]+-[a-f0-9]+\.[^/.]+$/);
        const image = await page.request.get(new URL(iconUrl, page.url()).href);
        assert.equal(image.status(), 200);
        assert.match(image.headers()["content-type"], /^image\//);
        assert.deepEqual(await image.body(), await readFile(resolve(brandRoot, brand.assets.icon)));
    }
    assert.deepEqual(errors, []);
});
