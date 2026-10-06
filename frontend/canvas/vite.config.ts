import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";
import { assetPrefixes, rebaseAssetLiterals } from "./asset-paths.mjs";

const root = dirname(fileURLToPath(import.meta.url));
const base = "/canvas-app/";
const manifest = JSON.parse(readFileSync(resolve(root, "source-manifest.json"), "utf8"));
const publicPrefixes = assetPrefixes(manifest.files);
const appVersion = readFileSync(resolve(root, "vendor/beeftv/VERSION"), "utf8").trim();
const appChangelog = readFileSync(resolve(root, "vendor/beeftv/CHANGELOG.md"), "utf8");

export default defineConfig(({ mode }) => {
    const env = loadEnv(mode, resolve(root, ".."), "");
    const canvasPort = Number(env.CANVAS_DEV_PORT || 8082);
    if (!Number.isInteger(canvasPort) || canvasPort < 1 || canvasPort > 65535) {
        throw new Error("CANVAS_DEV_PORT 必须为 1–65535 的整数。");
    }
    return {
        base,
        plugins: [
            {
                name: "canvas-public-assets",
                enforce: "pre",
                transform(code, id) {
                    if (!/[\\/]src[\\/].*\.[jt]sx?(?:\?|$)/.test(id)) return;
                    const adapted = rebaseAssetLiterals(code, publicPrefixes, base);
                    return adapted === code ? undefined : { code: adapted, map: null };
                },
                transformIndexHtml(html) {
                    return rebaseAssetLiterals(html, publicPrefixes, base);
                },
            },
            react(),
        ],
        define: {
            __APP_VERSION__: JSON.stringify(appVersion),
            __APP_CHANGELOG__: JSON.stringify(appChangelog),
            __BEEFTV_HEAVY_MEDIA_ENABLED__: "true",
            "import.meta.env.VITE_APP_VERSION": JSON.stringify(appVersion),
        },
        resolve: {
            dedupe: ["axios", "react", "react-dom"],
            alias: {
                "@": resolve(root, "src"),
                "@host": resolve(root, "../src"),
            },
        },
        server: {
            port: canvasPort,
            strictPort: true,
            watch: { ignored: ["**/.runtime/**"] },
            fs: { allow: [root, resolve(root, "..")] },
            proxy: { "/api": { target: env.API_PROXY_TARGET || "http://127.0.0.1:8000", changeOrigin: true } },
        },
        preview: { port: 4182, strictPort: true },
        build: {
            assetsDir: "static",
            rollupOptions: {
                output: {
                    onlyExplicitManualChunks: true,
                    manualChunks(id) {
                        if (/node_modules[\\/]@babel[\\/]runtime[\\/]/.test(id)) return "vendor-babel-runtime";
                        if (/node_modules[\\/](?:react(?:-dom|-router|-router-dom)?|scheduler|zustand|use-sync-external-store|@tanstack[\\/](?:query-core|react-query))[\\/]/.test(id)) return "vendor-react";
                        // 图标依赖 AntD 调色板，AntD 又依赖图标；保持同块避免跨块初始化循环。
                        if (/node_modules[\\/](?:lucide-react|antd|@ant-design|@rc-component|rc-[^\\/]+|dayjs)[\\/]/.test(id)) return "vendor-ui";
                    },
                },
            },
        },
    };
});
