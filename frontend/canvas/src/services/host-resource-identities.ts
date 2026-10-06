import { assertUserScope, captureUserScope, type CapturedUserScope } from "@/lib/user-scope-guard";
import { createResourceIdentityRegistry, type ResourceAliases } from "@/services/host-resource-identity-registry";

const registry = createResourceIdentityRegistry();

export function observeCanvasResourceAliases(scope: CapturedUserScope, aliases: ResourceAliases = {}) {
    assertUserScope(scope);
    registry.merge(scope, aliases);
}

export function canonicalCanvasResourceId(value: string) {
    return registry.canonical(captureUserScope(), value);
}

export function canonicalCanvasStorageKey(value: string | undefined) {
    if (!value?.startsWith("resource:")) return value;
    return `resource:${canonicalCanvasResourceId(value.slice("resource:".length))}`;
}

export function canvasResourceKeysEquivalent(left: string, right: string) {
    return canonicalCanvasStorageKey(left) === canonicalCanvasStorageKey(right);
}
