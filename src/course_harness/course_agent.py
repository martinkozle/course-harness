import subprocess
from dataclasses import dataclass
from pathlib import Path

from ag_ui.core import ActivitySnapshotEvent, EventType, StateSnapshotEvent
from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai import Agent, RunContext, ToolReturn
from pydantic_ai.models import Model

from course_harness.course_plan import (
    CoursePlan,
    CoursePlanInput,
    Lecture,
    LectureInput,
    create_course_plan,
    create_course_plan_file,
    initialize_workspace_history,
    read_course_plan,
    write_course_plan,
)


class CoursePlanLectureCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: str | None = Field(default=None, pattern=r"^lecture-[0-9a-f]{12}$")
    title: str = Field(min_length=1, max_length=200)
    group: str | None = Field(default=None, min_length=1, max_length=100)


class ReplaceCoursePlanCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=200)
    audience: str = Field(min_length=1, max_length=1000)
    goals: list[str] = Field(default_factory=list)
    outcomes: list[str] = Field(default_factory=list)
    lectures: list[CoursePlanLectureCommand] = Field(min_length=1)
    replace_all_lectures: bool = False


class CourseAgentState(BaseModel):
    course: CoursePlan | None = None


@dataclass
class CourseAgentDeps:
    course_state: CourseAgentState
    workspace: Path


def apply_course_plan_command(workspace: Path, command: ReplaceCoursePlanCommand) -> CoursePlan:
    existing = read_course_plan(workspace)
    draft = create_course_plan(
        CoursePlanInput(
            title=command.title,
            audience=command.audience,
            goals=command.goals,
            outcomes=command.outcomes,
            lectures=[
                LectureInput(title=lecture.title, group=lecture.group)
                for lecture in command.lectures
            ],
        )
    )
    if existing is None:
        try:
            initialize_workspace_history(workspace)
            create_course_plan_file(workspace, draft)
        except subprocess.CalledProcessError:
            raise RuntimeError("Course history could not be initialized") from None
        return draft

    existing_by_id = {lecture.id: lecture for lecture in existing.lectures}
    supplied_existing_ids = {
        lecture.id for lecture in command.lectures if lecture.id in existing_by_id
    }
    if existing.lectures and not supplied_existing_ids and not command.replace_all_lectures:
        raise ValueError(
            "Revising a Course Plan must preserve existing Lecture IDs. Set "
            "replace_all_lectures only when the Course Author explicitly approves replacing them."
        )
    used_ids: set[str] = set()
    lectures: list[Lecture] = []
    for command_lecture, draft_lecture in zip(command.lectures, draft.lectures, strict=True):
        identity = command_lecture.id
        if identity is not None and identity not in existing_by_id:
            raise ValueError(f"Lecture ID does not exist in this Course Plan: {identity}")
        if identity is not None and identity in used_ids:
            raise ValueError(f"Lecture ID cannot be used more than once: {identity}")
        identity = identity or draft_lecture.id
        used_ids.add(identity)
        lectures.append(
            Lecture(id=identity, title=command_lecture.title, group=command_lecture.group)
        )

    updated = CoursePlan(
        id=existing.id,
        title=command.title,
        audience=command.audience,
        goals=command.goals,
        outcomes=command.outcomes,
        lectures=lectures,
    )
    write_course_plan(workspace, updated)
    return updated


def create_course_agent() -> Agent[CourseAgentDeps, str]:
    agent = Agent(
        deps_type=CourseAgentDeps,
        name="course-agent",
        instructions=(
            "You are the persistent Course Agent. Collaborate with the Course Author to create "
            "and revise one Course Plan. Propose every authoritative change with "
            "replace_course_plan; the Course Author must approve it before it is applied. "
            "Preserve existing Lecture IDs supplied in shared state when revising a Lecture. "
            "Never set replace_all_lectures unless the Course Author explicitly asks to replace "
            "the entire Lecture spine. Explain the result clearly and concisely."
        ),
    )

    @agent.instructions
    async def current_course_plan(ctx: RunContext[CourseAgentDeps]) -> str:
        if ctx.deps.course_state.course is None:
            return "This Workspace does not have a Course Plan yet."
        return (
            "The current validated Course Plan follows. Preserve its Course and Lecture IDs "
            "when revising existing entities:\n"
            f"{ctx.deps.course_state.course.model_dump_json(indent=2)}"
        )

    @agent.tool(requires_approval=True)
    async def replace_course_plan(
        ctx: RunContext[CourseAgentDeps], command: ReplaceCoursePlanCommand
    ) -> ToolReturn:
        """Replace the Course Plan through one validated application command."""
        plan = apply_course_plan_command(ctx.deps.workspace, command)
        ctx.deps.course_state = CourseAgentState(course=plan)
        return ToolReturn(
            return_value="The validated Course Plan was saved.",
            metadata=[
                ActivitySnapshotEvent(
                    type=EventType.ACTIVITY_SNAPSHOT,
                    message_id=f"course-plan-{plan.id}",
                    activity_type="course-plan-change",
                    content={
                        "title": "Course Plan updated",
                        "detail": f"Saved {len(plan.lectures)} Lectures to course.yaml",
                    },
                ),
                StateSnapshotEvent(
                    type=EventType.STATE_SNAPSHOT,
                    snapshot=ctx.deps.course_state.model_dump(mode="json"),
                ),
            ],
        )

    return agent


def build_provider_model(configuration: object, api_key: str) -> Model:
    from course_harness.providers import ProviderConfiguration

    configured = ProviderConfiguration.model_validate(configuration)
    if configured.kind == "openrouter":
        from pydantic_ai.models.openrouter import OpenRouterModel
        from pydantic_ai.providers.openrouter import OpenRouterProvider

        return OpenRouterModel(
            configured.model,
            provider=OpenRouterProvider(api_key=api_key),
        )
    if configured.kind == "anthropic":
        from pydantic_ai.models.anthropic import AnthropicModel
        from pydantic_ai.providers.anthropic import AnthropicProvider

        return AnthropicModel(
            configured.model,
            provider=AnthropicProvider(api_key=api_key),
        )

    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers.openai import OpenAIProvider

    return OpenAIChatModel(
        configured.model,
        provider=OpenAIProvider(base_url=configured.base_url, api_key=api_key),
    )
