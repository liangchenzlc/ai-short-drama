import { http } from "@/services/api/request";
import type { CapturedUserScope } from "@/lib/user-scope-guard";

export type UserStyleProfile = {
    id: string;
    userId: string;
    name: string;
    description: string;
    coverUrl: string;
    tagsJson: string;
    profileJson: string;
    favorite: boolean;
    lastUsedAt?: string;
    revision: number;
    rowVersion: string;
    createdAt: string;
    updatedAt: string;
};

export function listStyleProfiles(expectedScope?: CapturedUserScope) {
    return http.get<{ profiles: UserStyleProfile[] }>("/style-profiles", { expectedScope });
}

export function createStyleProfile(profileJson: string, expectedScope?: CapturedUserScope, importKey?: string) {
    return http.post<{ profile: UserStyleProfile }>("/style-profiles", { profileJson, import_key: importKey }, { expectedScope });
}

export function updateStyleProfile(id: string, profileJson: string, expectedRowVersion: string, expectedScope?: CapturedUserScope) {
    return http.patch<{ profile: UserStyleProfile }>(`/style-profiles/${encodeURIComponent(id)}`, { profileJson, expected_row_version: expectedRowVersion }, { expectedScope });
}

export function setStyleProfileFavorite(id: string, favorite: boolean, expectedRowVersion: string, expectedScope?: CapturedUserScope) {
    return http.patch<{ id: string; favorite: boolean; rowVersion: string }>(`/style-profiles/${encodeURIComponent(id)}/favorite`, { favorite, expected_row_version: expectedRowVersion }, { expectedScope });
}

export function touchStyleProfile(id: string, expectedRowVersion: string, expectedScope?: CapturedUserScope) {
    return http.post<{ id: string; rowVersion: string }>(`/style-profiles/${encodeURIComponent(id)}/use`, { expected_row_version: expectedRowVersion }, { expectedScope });
}

export function deleteStyleProfile(id: string, expectedRowVersion: string, expectedScope?: CapturedUserScope) {
    return http.delete<{ id: string }>(`/style-profiles/${encodeURIComponent(id)}`, { data: { expected_row_version: expectedRowVersion }, expectedScope });
}

export type StylePreferences = { favorites: string[]; recent: string[] };
export function getStylePreferences(expectedScope?: CapturedUserScope) {
    return http.get<StylePreferences>("/style-profiles/preferences", { expectedScope });
}
export function writeStylePreference(presetId: string, action: "favorite" | "use", favorite?: boolean, expectedScope?: CapturedUserScope) {
    return http.post<StylePreferences>("/style-profiles/preferences", { preset_id: presetId, action, favorite }, { expectedScope });
}
