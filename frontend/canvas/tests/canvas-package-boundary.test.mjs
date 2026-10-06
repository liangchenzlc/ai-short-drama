import assert from "node:assert/strict";
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");

test("画布子包物理排除源整站页面，编辑器与共享资源选择器继续存在", () => {
    assert.deepEqual(readdirSync(resolve(root, "src/pages")), ["canvas"]);
    assert.equal(existsSync(resolve(root, "src/pages/canvas/index.tsx")), false);
    for (const path of ["src/pages/canvas/project.tsx", "src/components/assets/asset-library-picker-modal.tsx", "src/components/layout/app-providers.tsx", "src/components/layout/workspace-state.tsx"]) {
        assert.equal(existsSync(resolve(root, path)), true, path);
    }
});

test("编辑器不能通过侧栏、命令面板或路由预加载恢复源整站", () => {
    for (const name of ["app-top-nav.tsx", "workspace-sidebar-nav.tsx", "workspace-command-palette.tsx", "workspace-sidebar-update.tsx", "app-changelog-dialog.tsx", "app-changelog-modal.tsx", "model-setup-guide.tsx"]) {
        assert.equal(existsSync(resolve(root, "src/components/layout", name)), false, name);
    }
    const router = readFileSync(resolve(root, "src/router.tsx"), "utf8");
    assert.doesNotMatch(router, /import\(["']@\/pages\/(?:home|projects|agents|assets|settings|tasks|create)(?:[\/"'])/u);
    assert.doesNotMatch(router, /import\(["']@\/pages\/canvas["']/u);
    const preloader = readFileSync(resolve(root, "src/lib/workspace-route-modules.ts"), "utf8");
    assert.deepEqual([...preloader.matchAll(/import\(["']([^"']+)["']\)/gu)].map(match => match[1]), ["@/pages/canvas/project"]);
});
