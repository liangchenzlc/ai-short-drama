export type ResourceAliases = Record<string, string[]>;
type Scope = { userScope: string; epoch: number };

const maximumId = "18446744073709551615";
const identifier = (value: string) => /^[1-9][0-9]{0,19}$/.test(value) && (value.length < maximumId.length || value <= maximumId);

export function validateResourceAliases(aliases: unknown): asserts aliases is ResourceAliases {
    if (!aliases || typeof aliases !== "object" || Array.isArray(aliases)
        || Object.entries(aliases).some(([copy, origins]) => !identifier(copy) || !Array.isArray(origins) || origins.some(value => typeof value !== "string" || !identifier(value)))) {
        throw new Error("画布资源来源响应无效，请重新加载");
    }
}

/** Private provenance only establishes identity equivalence, never access to file bytes. */
export function createResourceIdentityRegistry() {
    let owner = "";
    const parents = new Map<string, string>();
    const scopeKey = (scope: Scope) => `${scope.userScope}\0${scope.epoch}`;
    const root = (value: string): string => {
        const path: string[] = [];
        let current = value;
        while (parents.has(current) && parents.get(current) !== current) {
            path.push(current);
            current = parents.get(current)!;
        }
        for (const item of path) parents.set(item, current);
        return current;
    };
    return {
        merge(scope: Scope, aliases: ResourceAliases) {
            validateResourceAliases(aliases);
            const entries = Object.entries(aliases);
            if (owner !== scopeKey(scope)) {
                owner = scopeKey(scope);
                parents.clear();
            }
            for (const [copy, origins] of entries) {
                for (const original of origins) {
                    const left = root(copy);
                    const right = root(original);
                    if (left !== right) parents.set(left, right);
                }
            }
        },
        canonical(scope: Scope, value: string) {
            return owner === scopeKey(scope) ? root(value) : value;
        },
    };
}
