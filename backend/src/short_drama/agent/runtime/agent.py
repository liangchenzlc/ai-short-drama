"""The only workbench create_agent composition point."""

from deepagents.middleware.subagents import SubAgentMiddleware
from langchain.agents import create_agent
from langchain.agents.middleware import (
    LLMToolSelectorMiddleware,
    ModelRetryMiddleware,
)

from short_drama.agent.gateway.contracts import AgentGatewayError
from short_drama.agent.runtime.context import AssistantState, RuntimeContext
from short_drama.agent.runtime.middleware import (
    ContextMiddleware,
    SkillSummarizationMiddleware,
    SkillToolsMiddleware,
)
from short_drama.agent.runtime.output_recovery import OutputRecoveryMiddleware

FIXED_TOOLS = {
    "runtime__ask_user",
    "runtime__wait_generation",
    "task",
    "execute",
    "runtime__load_skill",
    "runtime__search_skills",
    "runtime__release_skill",
    "runtime__read_skill_resource",
    "runtime__materialize_skill_resource",
    "runtime__import_resources",
    "runtime__export_artifacts",
}


def build_agent(*, model, tools, middleware, checkpointer, system_prompt):
    return create_agent(
        model=model,
        tools=tools,
        middleware=bound_model_middleware(middleware),
        system_prompt=system_prompt,
        state_schema=AssistantState,
        context_schema=RuntimeContext,
        checkpointer=checkpointer,
        name="workbench-assistant",
    )


def bound_model_middleware(middleware):
    # Selector、Skill依赖及委派system扩展均先完成，摘要才得到当轮真实包络。
    return [item for item in middleware if not isinstance(item, SkillSummarizationMiddleware)] + [
        item for item in middleware if isinstance(item, SkillSummarizationMiddleware)
    ]


def invalid_tool_selection(error):
    # The pinned official selector exposes this distinct error only after name validation.
    return type(error) is ValueError and str(error).startswith("Model selected invalid tools: [")


def retryable_model_output(error: Exception) -> bool:
    return invalid_tool_selection(error) or (
        isinstance(error, AgentGatewayError)
        and error.code == "invalid_agent_tool_calls"
        and not error.accepted_unknown
        and error.requests > 0
    )


def delegation_middleware(
    *, model, child_tools, child_middleware, backend, system_prompt, task_description
):
    child = create_agent(
        model=model,
        tools=child_tools,
        middleware=bound_model_middleware(child_middleware),
        context_schema=RuntimeContext,
        state_schema=AssistantState,
        checkpointer=None,
        system_prompt=system_prompt
        + "\n根据明确的委派目标完成子任务，只使用提供的必要背景和授权资源。"
        "返回结果摘要、已保存产物引用、失败与尚未解决事项。不要再次委派。",
        name="workbench-subtask",
    )
    return SubAgentMiddleware(
        backend=backend,
        subagents=[
            {
                "name": "workbench-task",
                "description": "隔离处理独立且上下文较多的子任务；返回摘要和产物。",
                "runnable": child,
            }
        ],
        task_description=task_description,
        # 上游默认只隔离messages，产品及中间件的其余state必须双向隔离。
        private_state_keys=frozenset(AssistantState.__annotations__) - {"messages"}
        | {
            "model_call_count",
            "thread_model_call_count",
            "run_model_call_count",
            "_summarization_event",
        },
    )


def assistant_middleware(*, model, tools, snapshot):
    names = {tool.name for tool in tools}
    limits = snapshot["assistant_settings"]
    return [
        ContextMiddleware(),
        OutputRecoveryMiddleware(snapshot),
        SkillSummarizationMiddleware(
            model=model,
            trim_tokens_to_summarize=None,
        ),
        ModelRetryMiddleware(
            max_retries=2,
            retry_on=retryable_model_output,
            on_failure="error",
            initial_delay=0,
        ),
        LLMToolSelectorMiddleware(
            model=model,
            system_prompt=limits["tool_selector_prompt"],
            max_tools=16,
            always_include=sorted(FIXED_TOOLS & names),
        ),
        SkillToolsMiddleware(tools),
    ]
