import { http } from "@/services/api/request";
import { assertUserScope, captureUserScope } from "@/lib/user-scope-guard";
import { openBeefAPIBrowser, trustedBeefAPIOrigin } from "@/services/host-beefapi-browser";

export type BeefAPIAccount = {
    id: string;
    username?: string;
    display_name?: string;
    email?: string;
};

export type BeefAPIConnectionState = "disconnected" | "pending" | "connected" | "expired" | "cancelled" | "rejected" | "store_error" | "catalog_failed" | "revoked";

export type BeefAPIConnectionSummary = {
    state: BeefAPIConnectionState | string;
    enterpriseOrigin?: string;
    userCode?: string;
    verificationUri?: string;
    expiresAt?: string;
    account?: BeefAPIAccount | null;
    keyName?: string;
    tokenId?: string;
    market?: string;
    walletUrl?: string;
    balance?: "unknown" | "zero" | "available" | string;
    catalogFailed?: boolean;
    errorReason?: string;
    connectedAt?: string;
    credentialRef?: string;
    hasCredential?: boolean;
};

export function getBeefAPIConnection(signal?: AbortSignal) {
    return http.get<BeefAPIConnectionSummary>("/beefapi/connection", { signal });
}

const browserOrigin = () => trustedBeefAPIOrigin(import.meta.env.DEV, typeof __CANVAS_BEEFAPI_TEST_ORIGIN__ === "undefined" ? undefined : __CANVAS_BEEFAPI_TEST_ORIGIN__);

function browserOwnership(signal?: AbortSignal) {
    const scope = captureUserScope();
    return () => {
        signal?.throwIfAborted();
        assertUserScope(scope);
    };
}

export function startBeefAPIConnection(signal?: AbortSignal, options?: { disconnectFirst?: boolean }) {
    const assertActive = browserOwnership(signal);
    return openBeefAPIBrowser(async () => {
        if (options?.disconnectFirst) {
            await disconnectBeefAPIConnection(signal);
            assertActive();
        }
        return http.post<BeefAPIConnectionSummary>("/beefapi/connection/start", {}, { signal });
    }, { kind: "authorization", url: summary => summary.verificationUri, shouldOpen: summary => summary.state === "pending", assertActive, trustedOrigin: browserOrigin() });
}

export function cancelBeefAPIConnection(signal?: AbortSignal) {
    return http.post<BeefAPIConnectionSummary>("/beefapi/connection/cancel", {}, { signal });
}

export function disconnectBeefAPIConnection(signal?: AbortSignal) {
    return http.post<BeefAPIConnectionSummary>("/beefapi/connection/disconnect", {}, { signal });
}

export async function openBeefAPIWallet(signal?: AbortSignal) {
    await openBeefAPIBrowser(() => http.post<{ enterpriseOrigin: string; walletUrl: string }>("/beefapi/connection/open-wallet", {}, { signal }), {
        kind: "wallet", url: summary => summary.walletUrl, assertActive: browserOwnership(signal), trustedOrigin: browserOrigin(),
    });
    return { opened: true };
}

export function beefAPIConnectionLabel(summary: BeefAPIConnectionSummary | null | undefined) {
    const state = summary?.state || "disconnected";
    switch (state) {
        case "pending":
            return summary?.userCode ? `请在浏览器中确认 ${summary.userCode}` : "请在浏览器中确认授权";
        case "connected":
            return connectedAccountLabel(summary);
        case "expired":
            return "授权已过期，请重新连接";
        case "cancelled":
            return "已取消本次连接";
        case "rejected":
            return "授权被拒绝";
        case "store_error":
            return "保存连接失败，请重试";
        case "catalog_failed":
            return "模型列表读取失败，请重试";
        case "revoked":
            return "连接已失效，请重新连接";
        default:
            return "未连接";
    }
}

function connectedAccountLabel(summary: BeefAPIConnectionSummary | null | undefined) {
    const account = summary?.account;
    const name = account?.display_name || account?.username || account?.email;
    if (name && summary?.balance === "zero") return `已连接 ${name}，余额为 0`;
    if (name) return `已连接 ${name}`;
    if (summary?.balance === "zero") return "已连接，余额为 0";
    return "已连接";
}
