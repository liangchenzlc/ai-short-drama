import assert from "node:assert/strict";
import test from "node:test";
import { canvasSettingsReturnTo, isModelSettingsPath, navigateToHostProjects, navigateToSettings, registerSettingsNavigationGuard, settingsPath } from "../src/lib/settings-navigation.ts";

test("宿主模型导航安全返回、保存等待、冲突与画布切换保护", async t => {
    const previous = globalThis.window;
    const destinations = [];
    globalThis.window = { location: { pathname: "/canvas-app/canvas/current-canvas", search: "", assign: to => destinations.push(to) } };
    try {
        await t.test("返回值仅接受画布编辑器路径，深链不接受任意URL", async () => {
            assert.equal(canvasSettingsReturnTo(), "/canvas-app/canvas/current-canvas");
            for (const path of ["https://evil.test", "//evil.test", "/projects/1", "/canvas-app/settings", "/canvas-app/canvas/..", "/canvas-app/canvas/%2F%2Fevil.test", "/canvas-app/canvas/%5Cevil", "/canvas-app/canvas/%252F", "/canvas-app/canvas/%00", "/canvas-app/canvas/%ZZ"]) assert.equal(canvasSettingsReturnTo(path), undefined);
            assert.equal(settingsPath(), "/ai_config?return_to=%2Fcanvas-app%2Fcanvas%2Fcurrent-canvas");
            assert.equal(isModelSettingsPath("/settings?section=channels"), true);
            assert.equal(isModelSettingsPath("/ai_config"), true);
            assert.equal(isModelSettingsPath("/assets"), false);
            window.location.pathname = "/canvas-app/settings";
            window.location.search = "?return_to=https%3A%2F%2Fevil.test";
            assert.equal(settingsPath(), "/ai_config");
            assert.equal(await navigateToSettings(), true);
            assert.equal(destinations.pop(), "/ai_config");
            window.location.search = "?return_to=%2Fcanvas-app%2Fcanvas%2Ffrom-link";
            assert.equal(settingsPath(), "/ai_config?return_to=%2Fcanvas-app%2Fcanvas%2Ffrom-link");
            window.location.pathname = "/canvas-app/canvas/current-canvas";
            window.location.search = "";
        });
        await t.test("保存完成后才全页离开，重复点击复用同次保存", async () => {
            let complete;
            let saves = 0;
            const stop = registerSettingsNavigationGuard(() => { saves++; return new Promise(resolve => { complete = resolve; }); }, assert.fail);
            try {
                const first = navigateToSettings();
                const second = navigateToSettings();
                assert.equal(first, second);
                assert.equal(saves, 1);
                assert.deepEqual(destinations, []);
                complete(true);
                assert.equal(await first, true);
                assert.equal(destinations.pop(), settingsPath());
            } finally { stop(); }
        });
        await t.test("保存失败或409保留页面，显示原因而不自动覆盖", async () => {
            const errors = [];
            let stop = registerSettingsNavigationGuard(async () => false, error => errors.push(error));
            assert.equal(await navigateToSettings(), false);
            stop();
            const conflict = new Error("409：当前草稿已保留");
            stop = registerSettingsNavigationGuard(async () => { throw conflict; }, error => errors.push(error));
            assert.equal(await navigateToSettings(), false);
            stop();
            assert.deepEqual(errors, [conflict]);
            assert.deepEqual(destinations, []);
        });
        await t.test("返回工作台等待保存，与模型配置共用冲突和重复点击保护", async () => {
            let complete;
            let saves = 0;
            const errors = [];
            let stop = registerSettingsNavigationGuard(() => { saves++; return new Promise(resolve => { complete = resolve; }); }, error => errors.push(error));
            const first = navigateToHostProjects();
            assert.equal(navigateToHostProjects(), first);
            assert.equal(saves, 1);
            assert.deepEqual(destinations, []);
            complete(true);
            assert.equal(await first, true);
            assert.equal(destinations.pop(), "/projects");
            stop();
            const conflict = new Error("409：当前草稿已保留");
            stop = registerSettingsNavigationGuard(async () => { throw conflict; }, error => errors.push(error));
            assert.equal(await navigateToHostProjects(), false);
            assert.deepEqual(destinations, []);
            assert.deepEqual(errors, [conflict]);
            stop();
        });
        await t.test("等待期间换画布或卸载旧保护不能跳走新画布", async () => {
            let complete;
            const stopOld = registerSettingsNavigationGuard(() => new Promise(resolve => { complete = resolve; }), assert.fail);
            const leaving = navigateToSettings();
            const stopNew = registerSettingsNavigationGuard(async () => true, assert.fail);
            stopOld();
            complete(true);
            assert.equal(await leaving, false);
            assert.deepEqual(destinations, []);
            assert.equal(await navigateToSettings(), true);
            assert.equal(destinations.pop(), settingsPath());
            stopNew();
        });
    } finally { globalThis.window = previous; }
});
