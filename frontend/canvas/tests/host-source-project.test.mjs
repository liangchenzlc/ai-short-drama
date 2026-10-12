import assert from "node:assert/strict";
import test from "node:test";
import { createCanvasSourceProjectTransport, readCanvasSourceEpisode, readCanvasSourceProject, sourceEpisodeChapter, sourceEpisodePlainText } from "../src/services/host-source-project.ts";

function fixture() {
    const context = { canvas_key: "来源画布-1", canvas_row_version: "18446744073709551615", owning_project_id: "9100000000000001", source_project_id: "9100000000000002" };
    const episode = { id: "9200000000000001", title: "第一集", position: 1, content_version: "18446744073709551615", kind: "episode" };
    const overview = { ...context, project: { id: context.source_project_id, row_version: "3", name: "真实来源项目", synopsis: "", aspect: "9:16", style_text: "自由文字画风" }, episodes: [episode], capabilities: { source_style_snapshot: "unavailable", source_asset_folders: "unavailable", source_assets: "not_included" } };
    const body = { ...context, episode, novel: { state: "present", content_format: "plain_text", plain_text: "  <舞台>&nbsp;\r\n下一行\t😀\u00a0  " } };
    const calls = [];
    let active = true;
    const assertCurrent = () => { if (!active) throw new Error("账号、画布或来源已切换"); };
    const host = { get: async (path, options) => { calls.push({ path, options }); return { data: path.includes("/episodes/") ? body : overview }; } };
    const transport = createCanvasSourceProjectTransport(host, "111", assertCurrent);
    return { context, episode, overview, body, calls, host, transport, assertCurrent, deactivate: () => { active = false; } };
}
const read = f => readCanvasSourceProject(f.transport, f.context, f.assertCurrent);
const readBody = f => readCanvasSourceEpisode(f.transport, f.context, f.episode, f.assertCurrent);

test("来源读取只走可信canvas key专用GET，冻结双项目及row version，保留十进制最大版本", async () => {
    const f = fixture();
    const result = await read(f);
    assert.deepEqual(result, f.overview);
    assert.notEqual(result, f.overview);
    assert.equal(result.episodes[0].content_version, "18446744073709551615");
    assert.deepEqual(f.calls[0].options.params, { expected_canvas_row_version: f.context.canvas_row_version, expected_owning_project_id: f.context.owning_project_id, expected_source_project_id: f.context.source_project_id });
    assert.deepEqual(f.calls[0].options.headers, { "X-Canvas-Actor": "111" });
    assert.match(f.calls[0].path, /^\/canvas-runtime\/canvas-projects\/%E6%9D%A5/);
    assert.equal(f.calls[0].path.split("canvas-runtime").length, 2);
});

test("正文GET冻结content version，HTML字样、实体、CRLF、首尾空白原样保留；章号保持一基", async () => {
    const f = fixture(); const controller = new AbortController();
    const result = await readCanvasSourceEpisode(f.transport, f.context, f.episode, f.assertCurrent, controller.signal);
    assert.equal(sourceEpisodePlainText(result), f.body.novel.plain_text);
    assert.equal(f.calls[0].options.params.expected_content_version, "18446744073709551615");
    assert.equal(f.calls[0].options.signal, controller.signal);
    assert.equal(f.calls[0].path.endsWith(`/source-project/episodes/${f.episode.id}`), true);
    assert.deepEqual(sourceEpisodeChapter(result.episode, f.context.source_project_id), { id: f.episode.id, title: "第一集", position: 1, projectId: f.context.source_project_id, contentVersion: f.episode.content_version });
});

test("真实空正文present可读取；缺小说missing单独反馈，不伪造正文", async () => {
    const f = fixture();
    f.body.novel.plain_text = "";
    assert.equal(sourceEpisodePlainText(await readBody(f)), "");
    f.body.novel = { state: "missing", content_format: "plain_text", plain_text: null };
    const result = await readBody(f);
    assert.equal(result.novel.state, "missing");
    assert.throws(() => sourceEpisodePlainText(result), /尚未保存小说正文/);
});

for (const field of ["canvas_key", "canvas_row_version", "owning_project_id", "source_project_id"]) {
    test(`overview与正文拒绝错配 ${field}`, async () => {
        const f = fixture(); f.overview[field] = "1"; f.body[field] = "1";
        await assert.rejects(read(f), /来源或版本不匹配/);
        await assert.rejects(readBody(f), /正文或版本已变化/);
        assert.equal(f.calls.length, 2);
    });
}

for (const [label, damage] of [
    ["number ID", f => { f.overview.project.id = 9100000000000002; }],
    ["zero row version", f => { f.overview.project.row_version = "0"; }],
    ["overflow content version", f => { f.episode.content_version = "18446744073709551616"; }],
    ["zero position", f => { f.episode.position = 0; }],
    ["noninteger position", f => { f.episode.position = 1.5; }],
    ["wrong unit kind", f => { f.episode.kind = "chapter"; }],
    ["duplicate episode", f => { f.overview.episodes.push({ ...f.episode }); }],
    ["duplicate position", f => { f.overview.episodes.push({ ...f.episode, id: "9200000000000002" }); }],
    ["private task field", f => { f.overview.tasks = []; }],
    ["fabricated style snapshot", f => { f.overview.capabilities.source_style_snapshot = "available"; }],
    ["fabricated folders", f => { f.overview.assetFolders = []; }],
]) {
    test(`目录拒绝 ${label}`, async () => { const f = fixture(); damage(f); await assert.rejects(read(f), /来源或版本不匹配/); assert.equal(f.calls.length, 1); });
}

for (const [label, damage] of [
    ["episode changed", f => { f.body.episode = { ...f.episode, id: "9200000000000002" }; }],
    ["content version changed", f => { f.body.episode = { ...f.episode, content_version: "2" }; }],
    ["title changed", f => { f.body.episode = { ...f.episode, title: "另一集" }; }],
    ["missing with empty string", f => { f.body.novel = { state: "missing", content_format: "plain_text", plain_text: "" }; }],
    ["present with null", f => { f.body.novel.plain_text = null; }],
    ["HTML format", f => { f.body.novel.content_format = "html"; }],
    ["private candidate field", f => { f.body.candidates = []; }],
]) {
    test(`正文拒绝 ${label}`, async () => { const f = fixture(); damage(f); await assert.rejects(readBody(f), /正文或版本已变化/); assert.equal(f.calls.length, 1); });
}

test("空目录是真实空结果；不补工作流、默认画风或目录", async () => {
    const f = fixture(); f.overview.episodes = []; f.overview.project.style_text = "";
    const result = await read(f);
    assert.deepEqual(result.episodes, []);
    assert.equal(result.project.style_text, "");
    assert.deepEqual(Object.keys(result).sort(), ["canvas_key", "canvas_row_version", "capabilities", "episodes", "owning_project_id", "project", "source_project_id"]);
});

for (const value of ["", " \t\r\n "]) {
    test(`ReadModel允许的空或纯空白名称、标题保持原值：${JSON.stringify(value)}`, async () => {
        const f = fixture(); f.overview.project.name = value; f.episode.title = value;
        const overview = await read(f); const body = await readBody(f);
        assert.equal(overview.project.name, value);
        assert.equal(overview.episodes[0].title, value);
        assert.equal(body.episode.title, value);
        assert.equal(sourceEpisodeChapter(body.episode, f.context.source_project_id).title, value);
    });
}

test("来源ReadModel的文字字段只校验string，不引入额外长度或去空白规则", async () => {
    const f = fixture();
    f.overview.project.name = " 项目 ".repeat(50);
    f.episode.title = "分集标题".repeat(80);
    f.overview.project.style_text = "画风说明".repeat(80);
    const overview = await read(f); const body = await readBody(f);
    assert.equal(overview.project.name, f.overview.project.name);
    assert.equal(overview.project.style_text, f.overview.project.style_text);
    assert.equal(body.episode.title, f.episode.title);
});

test("未绑定、无效canvas key及number版本均不发请求；旧source路径和双前缀拒绝", async () => {
    for (const damage of [f => { f.context.source_project_id = ""; }, f => { f.context.owning_project_id = ""; }, f => { f.context.canvas_key = "../other"; }, f => { f.context.canvas_row_version = 1; }]) {
        const f = fixture(); damage(f); await assert.rejects(read(f), /尚未关联/); assert.equal(f.calls.length, 0);
    }
    const f = fixture();
    for (const path of ["/canvas-runtime/projects/1", "/canvas-runtime/canvas-runtime/canvas-projects/x/source-project"]) await assert.rejects(f.transport.get(path, { params: {} }), /路径无效/);
    assert.equal(f.calls.length, 0);
});

test("读取前与等待响应期间账号/画布/双项目/revision变化，旧响应不返回", async () => {
    const first = fixture(); first.deactivate(); await assert.rejects(read(first), /已切换/); assert.equal(first.calls.length, 0);
    for (const damage of [f => f.deactivate(), ...["canvas_key", "canvas_row_version", "owning_project_id", "source_project_id"].map(key => f => { f.context[key] = "2"; })]) {
        for (const run of [read, readBody]) {
            const f = fixture(); const original = f.host.get;
            f.host.get = async (...args) => { const result = await original(...args); damage(f); return result; };
            await assert.rejects(run(f), /已切换|上下文已变化/); assert.equal(f.calls.length, 1);
        }
    }
});

test("读取中选中分集版本发生变化不返回；HTTP 403/404/409失败透传且不自动重试", async () => {
    const f = fixture(); const original = f.host.get;
    f.host.get = async (...args) => { const result = await original(...args); f.episode.content_version = "2"; return result; };
    await assert.rejects(readBody(f), /正文或版本已变化/);
    for (const status of [403, 404, 409]) {
        const failed = fixture(); let count = 0;
        failed.host.get = async () => { count++; throw new Error(`HTTP ${status}`); };
        await assert.rejects(read(failed), new RegExp(`HTTP ${status}`));
        await assert.rejects(readBody(failed), new RegExp(`HTTP ${status}`));
        assert.equal(count, 2);
    }
});
