import type { AiConfig, ModelChannel } from "@/stores/use-config-store";

const equal = (left: unknown, right: unknown) => JSON.stringify(left) === JSON.stringify(right);

function channelAcknowledgement(submitted: ModelChannel | undefined, current: ModelChannel, durable: ModelChannel | undefined): ModelChannel {
    if (!submitted || !durable) return current;
    const next = { ...current };
    for (const key of Object.keys(durable) as Array<keyof ModelChannel>) {
        if (key === "modelProfiles") continue;
        if (equal(current[key], submitted[key])) Object.assign(next, { [key]: durable[key] });
    }
    next.modelProfiles = current.modelProfiles?.map(profile => {
        const old = submitted.modelProfiles?.find(item => item.model === profile.model);
        const saved = durable.modelProfiles?.find(item => item.model === profile.model);
        if (!old || !saved) return profile;
        return { ...(equal(profile, old) ? saved : profile), logicalModelId: saved.logicalModelId };
    });
    return next;
}

/** 服务端 ACK 只替换已确认的字段；保存途中新增、删除及输入继续保留。 */
export function reconcileHostModelConfigAck(submitted: AiConfig, current: AiConfig, durable: AiConfig): AiConfig {
    const next = { ...current };
    for (const key of Object.keys(durable) as Array<keyof AiConfig>) {
        if (key === "channels") continue;
        if (equal(current[key], submitted[key])) Object.assign(next, { [key]: durable[key] });
    }
    const managed = durable.channels.filter(channel => channel.id === "beefapi").map(channel => {
        const previous = submitted.channels.find(item => item.id === channel.id);
        const draft = current.channels.find(item => item.id === channel.id);
        if (!previous || !draft) return channel;
        return {
            ...channel,
            enabled: equal(draft.enabled, previous.enabled) ? channel.enabled : draft.enabled,
            headers: equal(draft.headers, previous.headers) ? channel.headers : draft.headers,
        };
    });
    next.channels = [...durable.channels.filter(channel => channel.id.startsWith("host-")), ...current.channels.filter(channel => !channel.id.startsWith("host-") && channel.id !== "beefapi").map(channel => channelAcknowledgement(
        submitted.channels.find(item => item.id === channel.id),
        channel,
        durable.channels.find(item => item.id === channel.id),
    )), ...managed];
    return next;
}
