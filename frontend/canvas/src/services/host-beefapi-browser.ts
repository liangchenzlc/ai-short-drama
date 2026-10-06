const enterpriseOrigin = "https://enterprise.beefapi.com";

type BrowserPopup = Pick<Window, "closed" | "close" | "opener"> & {
    location: Pick<Location, "replace">;
};

export function trustedBeefAPIOrigin(development: boolean, testOrigin?: string): string {
    if (!development || !testOrigin) return enterpriseOrigin;
    const value = new URL(testOrigin);
    if (value.protocol !== "http:" || !["127.0.0.1", "localhost", "[::1]"].includes(value.hostname) || value.origin !== testOrigin) {
        throw new Error("BeefAPI 测试地址必须为显式的本机 HTTP origin");
    }
    return value.origin;
}

export function validateBeefAPIBrowserURL(value: string | undefined, kind: "authorization" | "wallet", trustedOrigin = enterpriseOrigin): string {
    if (!value) throw new Error("BeefAPI 未返回可打开的企业页面地址");
    const target = new URL(value);
    const rawPath = /^[a-z][a-z\d+.-]*:\/\/[^/?#]*([^?#]*)/i.exec(value)?.[1];
    const allowedPath = kind === "wallet" ? target.pathname === "/console/topup" && !value.includes("?") : target.pathname === "/desktop-auth" || target.pathname.startsWith("/desktop-auth/");
    if (target.origin !== trustedOrigin || target.username || target.password || value.includes("#") || rawPath === undefined || rawPath.includes("%") || value.includes("\\") || rawPath.split("/").some(part => part === "." || part === "..") || !allowedPath) {
        throw new Error("BeefAPI 返回了不受信任的企业页面地址");
    }
    return target.href;
}

/** 在原按钮手势内预开页面；请求失败、账号变化或无效地址均关闭空页面。 */
export async function openBeefAPIBrowser<T>(request: () => Promise<T>, options: {
    kind: "authorization" | "wallet";
    url: (result: T) => string | undefined;
    shouldOpen?: (result: T) => boolean;
    assertActive: () => void;
    trustedOrigin?: string;
    open?: () => BrowserPopup | null;
}): Promise<T> {
    options.assertActive();
    const popup = (options.open || (() => window.open("about:blank", "_blank")))();
    if (!popup) throw new Error("浏览器阻止了新页面，请允许弹出窗口后重试");
    try {
        popup.opener = null;
        if (popup.opener !== null) throw new Error("无法安全打开 BeefAPI 企业页面");
        const result = await request();
        options.assertActive();
        if (options.shouldOpen && !options.shouldOpen(result)) {
            popup.close();
            return result;
        }
        const target = validateBeefAPIBrowserURL(options.url(result), options.kind, options.trustedOrigin);
        if (popup.closed) throw new Error("企业页面已关闭，请重新打开");
        popup.location.replace(target);
        return result;
    } catch (error) {
        popup.close();
        throw error;
    }
}
