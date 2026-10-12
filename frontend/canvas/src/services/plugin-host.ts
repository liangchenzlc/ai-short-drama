import type { ResponseFunctionTool, ResponseInputMessage, ToolChoice } from "@/services/api/image";
import { runBackendToolGenerationTask } from "@/services/api/generation-task";
import { pluginStorageFor } from "@/lib/plugins/plugin-storage";
import type { AiConfig } from "@/stores/use-config-store";
import type { PluginHostContext, PluginInstallation, PluginTextRequest, RegisteredPlugin } from "@/lib/plugins/plugin-types";

export function createPluginHostContext(plugin: RegisteredPlugin, installation: PluginInstallation, aiConfig: AiConfig, canvas: { projectId: string; nodeId: string }): PluginHostContext {
    const permissions = new Set(plugin.manifest.permissions);
    return {
        manifest: plugin.manifest,
        permissions,
        storage: pluginStorageFor(plugin.manifest.id),
        config: installation.config,
        services: {
            ai: {
                text: {
                    requestToolResponse: async (request: PluginTextRequest) => {
                        if (!permissions.has("ai.text")) throw new Error("插件没有调用文本模型的权限");
                        const response = await runBackendToolGenerationTask({
                            projectId: canvas.projectId,
                            prompt: "提示词优化",
                            config: { ...aiConfig, model: request.model?.trim() || aiConfig.textModel },
                            messages: request.messages as ResponseInputMessage[],
                            tools: (request.tools || []) as ResponseFunctionTool[],
                            toolChoice: (request.toolChoice || "auto") as ToolChoice,
                            onDelta: request.onDelta,
                            signal: request.signal,
                            metadata: { source: plugin.manifest.id, nodeId: canvas.nodeId },
                        });
                        return {
                            content: response.content,
                            toolCalls: response.toolCalls.map((call) => ({ name: call.function.name, arguments: call.function.arguments })),
                        };
                    },
                },
            },
        },
    };
}
