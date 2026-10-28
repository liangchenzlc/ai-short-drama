export function AssistantLoading({ label }: { label: string }) {
  return (
    <p className="ai-assistant-meta ai-assistant-loading" role="status">
      <span>{label}</span>
      <span className="ai-assistant-loading-dots" aria-hidden="true">
        <span>.</span>
        <span>.</span>
        <span>.</span>
      </span>
    </p>
  );
}
