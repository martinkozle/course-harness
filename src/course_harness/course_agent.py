import enum
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
from course_harness.sources import Source, SourcesIndex


class AgentMode(enum.StrEnum):
    GUIDED = "guided"
    AUTONOMOUS = "autonomous"


class CoursePlanLectureCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: str | None = Field(default=None, pattern=r"^lecture-[0-9a-f]{12}$")
    title: str = Field(min_length=1, max_length=200)
    group: str | None = Field(default=None, min_length=1, max_length=100)
    source_focus: list[str] | None = Field(default=None)


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
    sources: list[Source] = Field(default_factory=list)


@dataclass
class CourseAgentDeps:
    course_state: CourseAgentState
    workspace: Path
    data_dir: Path
    cache_dir: Path


def apply_course_plan_command(workspace: Path, command: ReplaceCoursePlanCommand) -> CoursePlan:
    existing = read_course_plan(workspace)
    draft = create_course_plan(
        CoursePlanInput(
            title=command.title,
            audience=command.audience,
            goals=command.goals,
            outcomes=command.outcomes,
            lectures=[
                LectureInput(
                    title=lecture.title,
                    group=lecture.group,
                    source_focus=lecture.source_focus,
                )
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
            Lecture(
                id=identity,
                title=command_lecture.title,
                group=command_lecture.group,
                source_focus=command_lecture.source_focus,
            )
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


def _build_course_agent(*, requires_approval: bool) -> Agent[CourseAgentDeps, str]:
    agent = Agent(
        deps_type=CourseAgentDeps,
        name="course-agent",
        instructions=(
            "You are the persistent Course Agent. Collaborate with the Course Author to create "
            "and revise one Course Plan. Propose every authoritative change with "
            "replace_course_plan; the Course Author must approve it before it is applied. "
            "Preserve existing Lecture IDs supplied in shared state when revising a Lecture. "
            "Never set replace_all_lectures unless the Course Author explicitly asks to replace "
            "the entire Lecture spine. Explain the result clearly and concisely.\n\n"
            "You have access to admitted Course Sources. Use list_sources to see what is "
            "available, search_sources to find relevant content, and read_source_content to "
            "examine specific material. Reference Sources by their source_id when citing "
            "evidence. Never fabricate or guess source IDs — always confirm them through "
            "list_sources. Use admit_source only when the Course Author asks to promote a "
            "Library Resource to a Course Source."
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

    @agent.instructions
    async def current_sources(ctx: RunContext[CourseAgentDeps]) -> str:
        if not ctx.deps.course_state.sources:
            return (
                "No Sources have been admitted for this Course yet. Use admit_source to add them."
            )
        return (
            "The following Sources are admitted for this Course. Reference them by source_id "
            "when citing evidence:\n"
            f"{SourcesIndex(sources=ctx.deps.course_state.sources).model_dump_json(indent=2)}"
        )

    @agent.tool(requires_approval=requires_approval)
    async def replace_course_plan(
        ctx: RunContext[CourseAgentDeps], command: ReplaceCoursePlanCommand
    ) -> ToolReturn:
        """Replace the Course Plan through one validated application command."""
        plan = apply_course_plan_command(ctx.deps.workspace, command)
        ctx.deps.course_state = CourseAgentState(course=plan, sources=ctx.deps.course_state.sources)
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

    @agent.tool
    async def list_sources(ctx: RunContext[CourseAgentDeps]) -> ToolReturn:
        """List all Course Sources currently admitted for this Workspace."""
        if not ctx.deps.course_state.sources:
            return ToolReturn(return_value="No Sources have been admitted for this Course yet.")
        from course_harness.sources import SourcesIndex  # noqa: PLC0415

        index = SourcesIndex(sources=ctx.deps.course_state.sources)
        return ToolReturn(
            return_value=(f"Current Course Sources:\n{index.model_dump_json(indent=2)}"),
            metadata=[
                ActivitySnapshotEvent(
                    type=EventType.ACTIVITY_SNAPSHOT,
                    message_id=f"sources-list-{len(ctx.deps.course_state.sources)}",
                    activity_type="sources-list",
                    content={
                        "title": "Sources listed",
                        "detail": f"Found {len(ctx.deps.course_state.sources)} admitted Source(s)",
                    },
                ),
            ],
        )

    @agent.tool
    async def search_sources(
        ctx: RunContext[CourseAgentDeps], query: str, limit: int = 5
    ) -> ToolReturn:
        """Search the full text of Course Sources for relevant content."""
        from course_harness.search import enrich_search_results, search_raw  # noqa: PLC0415

        sources_by_version: dict[str, tuple[str, str, str]] = {}
        for s in ctx.deps.course_state.sources:
            sources_by_version[s.source_version_id] = (s.id, s.resource_id, s.label)

        hits = search_raw(ctx.deps.cache_dir, query, limit=limit)
        results = enrich_search_results(hits, sources_by_version)

        if not results:
            return ToolReturn(return_value=f"No results found for query: {query}")

        lines: list[str] = [f"Search results for '{query}':"]
        for r in results:
            lines.append(f"- {r.source_id} ({r.label}): {r.snippet}")
        return ToolReturn(
            return_value="\n".join(lines),
            metadata=[
                ActivitySnapshotEvent(
                    type=EventType.ACTIVITY_SNAPSHOT,
                    message_id=f"sources-search-{hash(query)}",
                    activity_type="sources-search",
                    content={
                        "title": f"Search: {query}",
                        "detail": f"Found {len(results)} result(s)",
                    },
                ),
            ],
        )

    @agent.tool
    async def read_source_content(
        ctx: RunContext[CourseAgentDeps],
        source_id: str,
        max_chars: int = 4000,
        line_start: int | None = None,
        line_end: int | None = None,
    ) -> ToolReturn:
        """Read the full text content of a Course Source, optionally bounded by line range."""
        source = next((s for s in ctx.deps.course_state.sources if s.id == source_id), None)
        if source is None:
            return ToolReturn(
                return_value=(
                    f"Source {source_id} was not found. Use list_sources to see available Sources."
                )
            )

        from course_harness.library import derived_dir  # noqa: PLC0415

        extracted = derived_dir(ctx.deps.cache_dir) / source.source_version_id / "extracted.md"
        if not extracted.is_file():
            return ToolReturn(
                return_value=(
                    f"Extracted content for Source {source_id} is not available. "
                    "Try reprocessing the underlying Resource."
                )
            )

        try:
            content = extracted.read_text(encoding="utf-8")
        except OSError, UnicodeError:
            return ToolReturn(return_value=f"Could not read content for Source {source_id}.")

        if line_start is not None or line_end is not None:
            lines = content.split("\n")
            start = max(0, line_start or 0)
            end = min(len(lines), line_end or len(lines))
            if start >= len(lines) or end <= start:
                return ToolReturn(
                    return_value=(
                        f"Invalid line range for Source {source_id} ({len(lines)} lines total)."
                    )
                )
            content = "\n".join(lines[start:end])

        if len(content) > max_chars:
            content = content[:max_chars] + "\n…[truncated]"

        return ToolReturn(
            return_value=content,
            metadata=[
                ActivitySnapshotEvent(
                    type=EventType.ACTIVITY_SNAPSHOT,
                    message_id=f"sources-read-{source_id}",
                    activity_type="sources-read",
                    content={
                        "title": f"Read: {source.label}",
                        "detail": f"Returned {len(content)} characters from {source.label}",
                    },
                ),
            ],
        )

    @agent.tool(requires_approval=requires_approval)
    async def admit_source(
        ctx: RunContext[CourseAgentDeps],
        resource_id: str,
        label: str | None = None,
    ) -> ToolReturn:
        """Admit a processed Library Resource as a Course Source."""
        from course_harness.sources import admit_source  # noqa: PLC0415

        try:
            source = admit_source(
                ctx.deps.workspace,
                ctx.deps.data_dir,
                resource_id,
                label=label,
            )
        except ValueError as error:
            return ToolReturn(return_value=f"Could not admit source: {error}")

        ctx.deps.course_state = CourseAgentState(
            course=ctx.deps.course_state.course,
            sources=[*ctx.deps.course_state.sources, source],
        )
        return ToolReturn(
            return_value=f"Source '{source.label}' admitted as {source.id}.",
            metadata=[
                ActivitySnapshotEvent(
                    type=EventType.ACTIVITY_SNAPSHOT,
                    message_id=f"sources-admit-{source.id}",
                    activity_type="sources-admit",
                    content={
                        "title": f"Source admitted: {source.label}",
                        "detail": f"Pinned version {source.source_version_id[:12]}",
                    },
                ),
                StateSnapshotEvent(
                    type=EventType.STATE_SNAPSHOT,
                    snapshot=ctx.deps.course_state.model_dump(mode="json"),
                ),
            ],
        )

    return agent


def create_course_agent() -> Agent[CourseAgentDeps, str]:
    return _build_course_agent(requires_approval=True)


def create_autonomous_course_agent() -> Agent[CourseAgentDeps, str]:
    return _build_course_agent(requires_approval=False)


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
