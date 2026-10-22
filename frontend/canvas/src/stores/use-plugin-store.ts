import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";

import { localForageStorageForScope } from "@/lib/localforage-storage";
import type { PluginInstallation, PluginManifest, PluginManifestV2 } from "@/lib/plugins/plugin-types";

export const PLUGIN_STORE_KEY = "infinite-canvas:plugin-store";

type PluginStore = {
    hydrated: boolean;
    installations: PluginInstallation[];
    ensurePlugin: (manifest: PluginManifest | PluginManifestV2) => void;
    updateConfig: (pluginId: string, config: Record<string, string | number | boolean>) => void;
    setError: (pluginId: string, error?: string) => void;
    removePlugin: (pluginId: string) => void;
};

function now() {
    return new Date().toISOString();
}

export const usePluginStore = create<PluginStore>()(
    persist(
        (set) => ({
            hydrated: false,
            installations: [],
            ensurePlugin: (manifest) =>
                set((state) => {
                    const current = state.installations.find((item) => item.manifest.id === manifest.id);
                    // 画布工具已不再受个人开关控制，内置插件一经注册即默认可用。
                    if (current && current.manifest.version === manifest.version && current.enabled) return { hydrated: true };
                    const timestamp = now();
                    const next: PluginInstallation = current
                        ? { ...current, manifest, enabled: true, updatedAt: timestamp }
                        : { manifest, enabled: true, config: {}, installedAt: timestamp, updatedAt: timestamp };
                    return { hydrated: true, installations: [...state.installations.filter((item) => item.manifest.id !== manifest.id), next] };
                }),
            updateConfig: (pluginId, config) =>
                set((state) => ({
                    installations: state.installations.map((item) => item.manifest.id === pluginId ? { ...item, config: { ...item.config, ...config }, updatedAt: now() } : item),
                })),
            setError: (pluginId, error) =>
                set((state) => ({
                    installations: state.installations.map((item) => item.manifest.id === pluginId ? { ...item, lastError: error, updatedAt: now() } : item),
                })),
            removePlugin: (pluginId) => set((state) => ({ installations: state.installations.filter((item) => item.manifest.id !== pluginId) })),
        }),
        {
            name: PLUGIN_STORE_KEY,
            storage: createJSONStorage(() => localForageStorageForScope()),
            partialize: (state) => ({ installations: state.installations }),
            onRehydrateStorage: () => (state) => {
                if (state) usePluginStore.setState({ hydrated: true });
            },
        },
    ),
);

export function isPluginEffectivelyEnabled(pluginId: string, fallbackEnabled?: boolean) {
    if (fallbackEnabled !== undefined) return fallbackEnabled;
    return Boolean(usePluginStore.getState().installations.find((item) => item.manifest.id === pluginId)?.enabled);
}
