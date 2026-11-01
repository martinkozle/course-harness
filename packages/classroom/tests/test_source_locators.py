"""Citations link through Source Locators, and each course lists its Sources."""

from typing import Any
from urllib.parse import quote, urlsplit

from fastapi.testclient import TestClient

from course_harness.core.presentation import (
    Presentation,
    SlideCitation,
    TitleSlide,
    write_presentation,
)
from course_harness.core.quiz import Quiz, QuizOption, QuizQuestion, write_quiz
from course_harness.core.release_validation import ReleaseSelection, Waiver
from course_harness.core.sources import Source, SourceLocator, SourcesIndex, write_sources_index
from course_harness.release_service import PublishReleaseRequest, publish_release
from course_harness.workspace_history import (
    RevisionCreateRequest,
    create_revision,
    record_app_authored_state,
)

LECTURE_ID = "lecture-aaaaaaaaaaaa"
PRESENTATION_ID = "presentation-aaaaaaaaaaaa"
QUESTION_ID = "question-aaaaaaaaaaaa"
CORRECT = "option-aaaaaaaaaaaa"
WRONG = "option-bbbbbbbbbbbb"
LINKED_ID = "source-aaaaaaaaaaaa"
UNLINKED_ID = "source-bbbbbbbbbbbb"
LOCATOR = SourceLocator(url="https://example.org/rubin-1974", doi="10.1037/h0037350")

LINKED = SlideCitation(source_id=LINKED_ID, label="Rubin (1974)")
OWN_URL = SlideCitation(source_id=LINKED_ID, label="Rubin (1974), p. 4", url="https://x.org/p4")
UNLINKED = SlideCitation(source_id=UNLINKED_ID, label="Lecture notes")


def _publish_with_sources(repository: Any) -> None:
    workspace = repository.workspace
    write_sources_index(
        workspace,
        SourcesIndex(
            sources=[
                Source(
                    id=LINKED_ID,
                    resource_id="resource-aaaaaaaaaaaa",
                    source_version_id="a" * 64,
                    label="Rubin (1974), Estimating causal effects",
                    admitted_at="2026-09-14T00:00:00+00:00",
                    locator=LOCATOR,
                ),
                Source(
                    id=UNLINKED_ID,
                    resource_id="resource-bbbbbbbbbbbb",
                    source_version_id="b" * 64,
                    label="Lecture notes",
                    admitted_at="2026-09-14T00:00:00+00:00",
                ),
            ]
        ),
    )
    write_presentation(
        workspace,
        Presentation(
            id=PRESENTATION_ID,
            lecture_id=LECTURE_ID,
            slides=[
                TitleSlide(
                    id="slide-aaaaaaaaaaaa",
                    title="Foundations",
                    citations=[LINKED, OWN_URL, UNLINKED],
                )
            ],
        ),
    )
    write_quiz(
        workspace,
        Quiz(
            lecture_id=LECTURE_ID,
            questions=[
                QuizQuestion(
                    id=QUESTION_ID,
                    prompt="What does randomisation remove?",
                    options=[
                        QuizOption(id=CORRECT, text="Confounding", correct=True),
                        QuizOption(
                            id=WRONG,
                            text="Sampling error",
                            explanation="It does not.",
                            citations=[LINKED],
                        ),
                    ],
                )
            ],
        ),
    )
    record_app_authored_state(workspace)
    create_revision(workspace, RevisionCreateRequest(summary="Cite Sources"))
    record_app_authored_state(workspace)
    publish_release(
        workspace=workspace,
        release_data_root=repository.root / "releases",
        templates_data_root=repository.root / "templates",
        evidence_line_counts={},
        request=PublishReleaseRequest(
            slug="fall-2026",
            name="Fall 2026",
            selection=ReleaseSelection(lecture_ids=[LECTURE_ID], artifact_ids=[PRESENTATION_ID]),
            waivers=[
                Waiver(
                    finding_id=f"citation.source-without-locator:{UNLINKED_ID}",
                    justification="Handed out in class.",
                )
            ],
        ),
    )
    repository.push()


def _learner(make_client: Any, admin: TestClient, repository: Any) -> TestClient:
    attached = admin.post(
        "/api/admin/courses", json={"repo_url": str(repository.remote), "slug": "causal"}
    )
    assert attached.status_code == 201, attached.text
    url = admin.post("/api/admin/courses/causal/links/learner").json()["url"]
    client = make_client()
    assert client.get(urlsplit(url).path).status_code == 303
    return client


def test_citations_link_to_their_own_url_or_their_sources_locator(
    make_client: Any, admin: TestClient, course_repository: Any
) -> None:
    _publish_with_sources(course_repository)
    learner = _learner(make_client, admin, course_repository)

    lecture = learner.get(f"/api/c/causal/lectures/{LECTURE_ID}").json()
    citations = lecture["slides"][0]["slide"]["citations"]
    assert [citation["url"] for citation in citations] == [
        "https://example.org/rubin-1974",
        "https://x.org/p4",
        None,
    ]

    answer = learner.post(
        "/api/c/causal/answers",
        json={
            "lecture_id": LECTURE_ID,
            "question_id": QUESTION_ID,
            "option_id": WRONG,
            "learner_name": "Ada Lovelace",
            "student_id": "s-1001",
        },
    ).json()
    assert [citation["url"] for citation in answer["citations"]] == [
        "https://example.org/rubin-1974"
    ]
    progress = learner.get(
        "/api/c/causal/progress", headers={"X-Classroom-Student": quote("s-1001")}
    ).json()
    (tried,) = progress["questions"][0]["tried"]
    assert tried["citations"][0]["url"] == "https://example.org/rubin-1974"


def test_the_course_lists_its_sources_with_their_public_links(
    make_client: Any, admin: TestClient, course_repository: Any
) -> None:
    _publish_with_sources(course_repository)
    learner = _learner(make_client, admin, course_repository)

    assert learner.get("/api/c/causal").json()["sources"] == [
        {
            "id": LINKED_ID,
            "label": "Rubin (1974), Estimating causal effects",
            "url": "https://example.org/rubin-1974",
            "doi": "10.1037/h0037350",
            "arxiv_id": None,
        },
        {
            "id": UNLINKED_ID,
            "label": "Lecture notes",
            "url": None,
            "doi": None,
            "arxiv_id": None,
        },
    ]


def test_a_release_without_locators_lists_its_sources_unlinked(
    make_client: Any, admin: TestClient, course_repository: Any
) -> None:
    course_repository.publish("fall-2026", "Fall 2026")
    learner = _learner(make_client, admin, course_repository)

    assert learner.get("/api/c/causal").json()["sources"] == []
    lecture = learner.get(f"/api/c/causal/lectures/{LECTURE_ID}").json()
    assert lecture["slides"][0]["slide"]["citations"] == []
