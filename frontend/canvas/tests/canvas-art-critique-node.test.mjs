import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";
import ts from "typescript";

const source = readFileSync(new URL("../src/components/canvas/nodes/ai-art-critique-node.tsx", import.meta.url), "utf8");
const code = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText;

function render({ localEnabled = false, effectiveEnabled, runtimeStatus } = {}) {
    const state = {
        installations: [{ manifest: { id: "ai-art-critique" }, enabled: localEnabled }],
        pluginStates: effectiveEnabled === undefined ? {} : { "ai-art-critique": { effectiveEnabled } },
        runtimeStatuses: runtimeStatus === undefined ? {} : { "ai-art-critique": runtimeStatus },
    };
    const theme = { node: {}, toolbar: {}, accent: {} };
    const opened = [];
    const jsx = (type, props) => ({ type, props });
    const exports = {};
    const modules = {
        "react/jsx-runtime": { jsx, jsxs: jsx },
        "lucide-react": {},
        "@/lib/canvas-theme": { canvasThemes: { dark: theme } },
        "@/lib/art-critique/contracts": {
            ART_CRITIQUE_PLUGIN_ID: "ai-art-critique", artCritiqueSourceFingerprint: () => "fixed-source",
            createDefaultArtCritiqueState: () => ({ status: "idle" }),
            isArtCritiqueImageInput: node => node.type === "image", artCritiqueStageLabel: value => value,
        },
        "@/stores/use-plugin-store": { usePluginStore: selector => selector(state) },
        "@/stores/canvas/use-canvas-theme-store": { useActiveTheme: () => "dark" },
        "@/types/canvas": { CanvasNodeType: { Image: "image" } },
        "../canvas-node-action-context": { useCanvasNodeActions: () => ({ openArtCritique: node => opened.push(node) }) },
        "../canvas-node-graph-context": { useUpstreamNodes: () => [{ id: "source", type: "image", metadata: { storageKey: "resource:1" } }] },
    };
    const context = vm.createContext({ exports, require: name => {
        assert.ok(name in modules, name);
        return modules[name];
    } });
    vm.runInContext(code, context);
    const node = { id: "analysis", type: "ai-art-critique", metadata: {} };
    const view = exports.ArtCritiqueNodeContent({ node });
    const button = view.props.children.find(child => child?.type === "button");
    return { button, node, opened };
}

test("服务端启用时，旧的本地关闭状态不能阻止原审美节点入口", () => {
    const { button, node, opened } = render({ localEnabled: false, effectiveEnabled: true });
    assert.equal(button.props.disabled, false);
    button.props.onClick({ stopPropagation() {} });
    assert.deepEqual(opened, [node]);
});

test("服务端关闭时，旧的本地启用状态不能重新启用原审美节点", () => {
    assert.equal(render({ localEnabled: true, effectiveEnabled: false }).button.props.disabled, true);
});

test("没有新状态合同的本地运行保留原开关，旧服务端状态仍优先", () => {
    assert.equal(render({ localEnabled: true }).button.props.disabled, false);
    assert.equal(render({ localEnabled: true, runtimeStatus: "disabled" }).button.props.disabled, true);
    assert.equal(render({ localEnabled: false, runtimeStatus: "enabled" }).button.props.disabled, false);
});
