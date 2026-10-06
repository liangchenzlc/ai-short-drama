import { http } from "@/services/api/request";
import type { WorkspaceCapabilityContract } from "@/services/workspace-mode";
import type { AiConfig } from "@/stores/use-config-store";
import type { FeatureAvailability, LocalUser, RuntimeLimits } from "@/stores/use-user-store";
import { getAuthenticatedCanvasBootstrap } from "@/services/host-session";

export type LocalWorkspace = {
    id: string;
    name: string;
    owner: string;
    storage: "mysql";
};

export type WorkspaceBootstrapPayload = {
    contractVersion: number;
    profile: WorkspaceCapabilityContract["profile"];
    capabilities: WorkspaceCapabilityContract["capabilities"];
    user: LocalUser;
    workspace: LocalWorkspace;
    storageMode: "remote";
    runtimeLimits?: RuntimeLimits;
    features?: FeatureAvailability;
};

export function getWorkspaceBootstrap() {
    return getAuthenticatedCanvasBootstrap();
}

export async function getLocalModelConfig() {
    const { sourceModelConfig } = await import("@/services/host-model-config");
    const value = await http.get<import("@/services/host-model-config").HostModelConfig>("/workspace/model-config");
    return { config: sourceModelConfig(value), revision: value.row_version, health: "ready" as const, source: "host" as const };
}

export type LocalModelConfigPayload = {
	config: AiConfig;
	revision: string;
	health: "ready" | "default" | "migrated" | "recovered";
	source: "host";
};

export async function saveLocalModelConfig(config: AiConfig, expectedRevision: string) {
    const { modelPreferences, sourceModelConfig } = await import("@/services/host-model-config");
    const result = await http.put<import("@/services/host-model-config").HostModelConfig>("/workspace/model-config", { preferences: modelPreferences(config), expected_row_version: expectedRevision });
    return { saved: true, revision: result.row_version, config: sourceModelConfig(result) };
}
