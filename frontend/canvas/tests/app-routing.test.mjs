import assert from "node:assert/strict";
import test from "node:test";
import { appHref, appPathname, appPathnameFrom } from "../src/lib/app-routing.ts";

const runtime = { location: {
    origin: "https://studio.example", protocol: "https:",
    pathname: "/canvas-app/canvas/123", search: "?node=abc",
} };

test("画布独立入口保留源组件识别的路径并生成可刷新的同源链接", () => {
    assert.equal(appPathname(runtime), "/canvas/123");
    assert.equal(appHref("/canvas/456?node=abc", runtime), "https://studio.example/canvas-app/canvas/456?node=abc");
    assert.equal(appPathnameFrom(appHref("/canvas/456", runtime), runtime), "/canvas/456");
    assert.equal(appHref("/canvas-app/canvas/456", runtime), "https://studio.example/canvas-app/canvas/456");
});

test("路径前缀只能完整匹配，API 和主项目路由不被误识别", () => {
    assert.equal(appPathnameFrom("/canvas-application/canvas/1", runtime), "/canvas-application/canvas/1");
    assert.equal(appPathnameFrom("/api/v1/canvases", runtime), "/api/v1/canvases");
    assert.equal(appPathnameFrom("/projects/1", runtime), "/projects/1");
});
