import os
import subprocess
from pathlib import Path
from shutil import move
from typing import Literal

import httpx2
import pytest

import course_harness.canonical_mutation as canonical_mutation
import course_harness.workspace_history as history
from course_harness.app import create_app
from course_harness.course_plan import (
    CoursePlanInput,
    LectureInput,
    create_course_plan,
    read_course_plan,
    write_course_plan,
)
from course_harness.presentation import Presentation, SlideCitation, TitleSlide, write_presentation
from course_harness.workspace_history import (
    DriftAcceptRequest,
    ReconciliationApplyRequest,
    ReconciliationFile,
    RevisionCreateRequest,
    SelectiveRevertRequest,
    WorkspaceDriftChangedError,
    WorkspaceHistoryError,
    WorkspaceHistoryNotInitializedError,
    abandon_run_boundary,
    accept_workspace_drift,
    apply_reconciliation,
    begin_run_boundary,
    capture_reconciliation_context,
    checkpoint_run_mutation,
    create_recovery_snapshot,
    create_revision,
    list_revisions,
    mark_run_mutation,
    read_current_state,
    record_app_authored_paths,
    record_app_authored_state,
    restore_revision,
    revert_current_path,
)


def initialize_repository(workspace: Path) -> None:
    subprocess.run(
        ["git", "init", "--quiet", "--initial-branch=main", str(workspace)],
        check=True,
        capture_output=True,
        text=True,
    )


def commit_all(workspace: Path) -> None:
    subprocess.run(["git", "add", "."], cwd=workspace, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Course Harness tests",
            "-c",
            "user.email=course-harness-tests@example.invalid",
            "commit",
            "--quiet",
            "-m",
            "Initial Course",
        ],
        cwd=workspace,
        check=True,
    )


def write_valid_course(workspace: Path, title: str = "Statistical learning") -> None:
    write_course_plan(
        workspace,
        create_course_plan(
            CoursePlanInput(
                title=title,
                audience="Analysts",
                lectures=[LectureInput(title="Regression")],
            )
        ),
    )


def git(workspace: Path, *arguments: str) -> None:
    subprocess.run(["git", *arguments], cwd=workspace, check=True)


def test_current_state_is_clean_in_an_unborn_repository(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    initialize_repository(workspace)

    state = read_current_state(workspace)

    assert state.clean is True
    assert state.changes == []
    assert state.validation.model_dump() == {"valid": True, "findings": []}


def test_current_state_derives_all_fields_from_one_canonical_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    initialize_repository(workspace)
    write_valid_course(workspace, "Captured valid state")
    original = history._capture_canonical_blobs
    calls = 0
    reviewed: dict[str, history._CapturedEntry] = {}

    def capture_then_change(path: Path) -> dict[str, history._CapturedEntry]:
        nonlocal calls, reviewed
        calls += 1
        captured = original(path)
        reviewed = captured
        (workspace / "course.yaml").write_text("not: [yaml", encoding="utf-8")
        return captured

    monkeypatch.setattr(history, "_capture_canonical_blobs", capture_then_change)
    state = read_current_state(workspace)

    assert calls == 1
    assert state.validation.valid is True
    assert state.changes[0].line_count == 14
    assert state.drift_id == history._drift_id(None, history._fingerprints(reviewed))


def test_recovery_snapshot_is_hidden_and_does_not_advance_main(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    initialize_repository(workspace)
    write_valid_course(workspace)

    snapshot = create_recovery_snapshot(workspace)

    assert len(snapshot) == 40
    assert list_revisions(workspace) == []
    assert (
        subprocess.run(
            ["git", "show-ref", "--verify", "--quiet", "refs/heads/main"], cwd=workspace
        ).returncode
        == 1
    )
    assert (
        _git_output(
            workspace, "for-each-ref", "--format=%(objectname)", "refs/course-harness/recovery"
        ).strip()
        == snapshot
    )


def test_provenance_classifies_and_accepts_valid_workspace_drift(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    initialize_repository(workspace)
    write_valid_course(workspace, "Baseline")
    record_app_authored_state(workspace)
    assert read_current_state(workspace).drift == "clean"

    write_valid_course(workspace, "External change")
    drift = read_current_state(workspace)
    assert drift.drift == "drift"
    assert drift.drift_changes[0].path == "course.yaml"
    revision = accept_workspace_drift(
        workspace,
        DriftAcceptRequest(summary="Accept external edit", drift_id=drift.drift_id or ""),
    )
    assert revision.id in {item.id for item in list_revisions(workspace)}
    assert read_current_state(workspace).drift == "clean"


def test_record_app_authored_paths_preserves_unrelated_external_drift(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    initialize_repository(workspace)
    write_valid_course(workspace, "Baseline")
    create_revision(workspace, RevisionCreateRequest(summary="Initial"))
    record_app_authored_state(workspace)
    baseline = (workspace / "course.yaml").read_bytes()
    write_valid_course(workspace, "External course")
    (workspace / "sources.yaml").write_text("version: 1\nsources: []\n", encoding="utf-8")

    (workspace / "course.yaml").write_bytes(baseline)
    record_app_authored_paths(workspace, {"course.yaml"})

    state = read_current_state(workspace)
    assert state.drift == "drift"
    assert [entry.path for entry in state.drift_changes] == ["sources.yaml"]


def test_reconciliation_repairs_invalid_drift_and_records_revision(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    initialize_repository(workspace)
    write_valid_course(workspace, "Baseline")
    create_revision(workspace, RevisionCreateRequest(summary="Initial"))
    record_app_authored_state(workspace)
    (workspace / "course.yaml").write_text("not: [yaml", encoding="utf-8")

    context = capture_reconciliation_context(workspace)
    assert context.findings
    valid_course = history._tree_blobs(workspace, history._head_oid(workspace) or "")[
        "course.yaml"
    ].content.decode("utf-8")
    revision = apply_reconciliation(
        workspace,
        ReconciliationApplyRequest(
            drift_id=context.drift_id,
            summary="Repair external Course file",
            entries=[
                ReconciliationFile(
                    path="course.yaml",
                    content=valid_course,
                )
            ],
        ),
    )

    assert revision.id in {item.id for item in list_revisions(workspace)}
    assert read_current_state(workspace).drift == "clean"


def test_reconciliation_preserves_a_same_path_external_edit_during_apply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    initialize_repository(workspace)
    write_valid_course(workspace, "Baseline")
    create_revision(workspace, RevisionCreateRequest(summary="Initial"))
    record_app_authored_state(workspace)
    write_valid_course(workspace, "Reviewed external edit")
    context = capture_reconciliation_context(workspace)
    repaired = history._tree_blobs(workspace, history._head_oid(workspace) or "")[
        "course.yaml"
    ].content.decode("utf-8")

    def external_edit(path: Path, _expected: object) -> None:
        monkeypatch.setattr(canonical_mutation, "after_precondition_check", None)
        write_valid_course(path, "Later external edit")

    monkeypatch.setattr(canonical_mutation, "after_precondition_check", external_edit)
    with pytest.raises(canonical_mutation.CanonicalMutationConflict):
        apply_reconciliation(
            workspace,
            ReconciliationApplyRequest(
                drift_id=context.drift_id,
                summary="Repair reviewed edit",
                entries=[ReconciliationFile(path="course.yaml", content=repaired)],
            ),
        )

    current = read_course_plan(workspace)
    assert current is not None
    assert current.title == "Later external edit"


def test_canonical_multi_file_conflict_compensates_without_clobbering_external_edit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    course = workspace / "course.yaml"
    sources = workspace / "sources.yaml"
    course.write_bytes(b"course-before")
    sources.write_bytes(b"sources-before")
    expected = canonical_mutation.capture_canonical_files(
        workspace, {"course.yaml", "sources.yaml"}
    )
    original_replace = canonical_mutation._replace_canonical_file

    def replace_then_edit(
        path: Path, relative_path: str, output: canonical_mutation.CanonicalFile
    ) -> None:
        original_replace(path, relative_path, output)
        if relative_path == "course.yaml":
            sources.write_bytes(b"external-sources")

    monkeypatch.setattr(canonical_mutation, "_replace_canonical_file", replace_then_edit)

    with pytest.raises(canonical_mutation.CanonicalMutationConflict):
        canonical_mutation.apply_canonical_mutation(
            workspace,
            expected=expected,
            updates={"course.yaml": b"course-after", "sources.yaml": b"sources-after"},
        )

    assert course.read_bytes() == b"course-before"
    assert sources.read_bytes() == b"external-sources"


def test_conditional_restore_preserves_the_original_file_mode(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    course = workspace / "course.yaml"
    course.write_bytes(b"before")
    course.chmod(0o600)
    before = canonical_mutation.capture_canonical_file(workspace, "course.yaml")
    written = canonical_mutation.apply_canonical_mutation(
        workspace,
        expected={"course.yaml": before},
        updates={"course.yaml": b"after"},
    )

    assert canonical_mutation.restore_canonical_mutation(
        workspace,
        expected_current=written,
        restore={"course.yaml": before},
    )
    assert course.read_bytes() == b"before"
    assert course.stat().st_mode & 0o777 == 0o600


def test_canonical_mutations_bound_individual_and_aggregate_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    (workspace / "course.yaml").write_bytes(b"1234")
    (workspace / "sources.yaml").write_bytes(b"5678")
    before = canonical_mutation.capture_canonical_file(workspace, "course.yaml")

    monkeypatch.setattr(canonical_mutation, "MAX_CANONICAL_FILE_BYTES", 4)
    with pytest.raises(canonical_mutation.CanonicalMutationUnsafe, match="too large to save"):
        canonical_mutation.apply_canonical_mutation(
            workspace,
            expected={"course.yaml": before},
            updates={"course.yaml": b"12345"},
        )
    assert (workspace / "course.yaml").read_bytes() == b"1234"

    monkeypatch.setattr(canonical_mutation, "MAX_CANONICAL_TOTAL_BYTES", 7)
    with pytest.raises(canonical_mutation.CanonicalMutationUnsafe, match="too large to inspect"):
        canonical_mutation.capture_canonical_files(workspace, {"course.yaml", "sources.yaml"})


def test_canonical_mutation_never_follows_a_replaced_parent_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "course"
    presentations = workspace / "presentations"
    presentations.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_target = outside / "presentation-safe.yaml"
    outside_target.write_bytes(b"outside")
    expected = canonical_mutation.capture_canonical_file(
        workspace, "presentations/presentation-safe.yaml"
    )

    def replace_parent(_workspace: Path, _expected: object) -> None:
        presentations.rmdir()
        presentations.symlink_to(outside, target_is_directory=True)

    monkeypatch.setattr(canonical_mutation, "after_precondition_check", replace_parent)
    with pytest.raises(canonical_mutation.CanonicalMutationUnsafe):
        canonical_mutation.apply_canonical_mutation(
            workspace,
            expected={"presentations/presentation-safe.yaml": expected},
            updates={"presentations/presentation-safe.yaml": b"application"},
        )

    assert outside_target.read_bytes() == b"outside"


def test_reconciliation_rolls_back_invalid_and_rejects_stale_or_unsafe_paths(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    initialize_repository(workspace)
    write_valid_course(workspace)
    create_revision(workspace, RevisionCreateRequest(summary="Initial"))
    record_app_authored_state(workspace)
    write_valid_course(workspace, "External")
    context = capture_reconciliation_context(workspace)
    before = (workspace / "course.yaml").read_bytes()

    with pytest.raises(ValueError, match="invalid"):
        apply_reconciliation(
            workspace,
            ReconciliationApplyRequest(
                drift_id=context.drift_id,
                summary="Bad repair",
                entries=[ReconciliationFile(path="course.yaml", content="not: [yaml")],
            ),
        )
    assert (workspace / "course.yaml").read_bytes() == before
    with pytest.raises(ValueError, match="unique canonical"):
        apply_reconciliation(
            workspace,
            ReconciliationApplyRequest(
                drift_id=context.drift_id,
                summary="Bad path",
                entries=[ReconciliationFile(path="../outside", content="x")],
            ),
        )
    write_valid_course(workspace, "Newer external")
    with pytest.raises(ValueError, match="changed"):
        apply_reconciliation(
            workspace,
            ReconciliationApplyRequest(
                drift_id=context.drift_id,
                summary="Stale repair",
                entries=[ReconciliationFile(path="course.yaml", content=before.decode())],
            ),
        )


def test_reconciliation_can_restore_a_deleted_canonical_file_from_trusted_history(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    initialize_repository(workspace)
    write_valid_course(workspace, "Baseline")
    create_revision(workspace, RevisionCreateRequest(summary="Initial"))
    record_app_authored_state(workspace)
    (workspace / "course.yaml").unlink()

    context = capture_reconciliation_context(workspace)

    assert context.missing_paths == ["course.yaml"]
    assert [entry.path for entry in context.baseline_files] == ["course.yaml"]
    restored = context.baseline_files[0].content
    assert restored is not None
    apply_reconciliation(
        workspace,
        ReconciliationApplyRequest(
            drift_id=context.drift_id,
            summary="Restore externally deleted Course Plan",
            entries=[ReconciliationFile(path="course.yaml", content=restored)],
        ),
    )

    restored_plan = read_course_plan(workspace)
    assert restored_plan is not None
    assert restored_plan.title == "Baseline"
    assert read_current_state(workspace).drift == "clean"


def test_reconciliation_rejects_changes_arriving_after_the_reviewed_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    initialize_repository(workspace)
    write_valid_course(workspace, "Baseline")
    create_revision(workspace, RevisionCreateRequest(summary="Initial"))
    record_app_authored_state(workspace)
    write_valid_course(workspace, "Reviewed external edit")
    context = capture_reconciliation_context(workspace)
    original = history._begin_transaction

    def begin_after_concurrent_change(path: Path) -> tuple[str, str]:
        (workspace / "sources.yaml").write_text("version: 1\nsources: []\n", encoding="utf-8")
        return original(path)

    monkeypatch.setattr(history, "_begin_transaction", begin_after_concurrent_change)
    with pytest.raises(WorkspaceDriftChangedError, match="changed"):
        apply_reconciliation(
            workspace,
            ReconciliationApplyRequest(
                drift_id=context.drift_id,
                summary="Apply reviewed repair",
                entries=[
                    ReconciliationFile(
                        path="course.yaml",
                        content=(workspace / "course.yaml").read_text(encoding="utf-8"),
                    )
                ],
            ),
        )

    assert _git_output(workspace, "show", "-s", "--format=%s", "HEAD").strip() == "Initial"
    assert (workspace / "sources.yaml").is_file()


def test_reconciliation_post_commit_failure_recovers_committed_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    initialize_repository(workspace)
    write_valid_course(workspace, "Baseline")
    create_revision(workspace, RevisionCreateRequest(summary="Initial"))
    record_app_authored_state(workspace)
    write_valid_course(workspace, "External")
    context = capture_reconciliation_context(workspace)
    original = history._persist_committed_provenance

    def fail_after_ref(*_args: object) -> None:
        raise OSError("metadata write failed")

    monkeypatch.setattr(history, "_persist_committed_provenance", fail_after_ref)
    with pytest.raises(OSError, match="metadata write failed"):
        apply_reconciliation(
            workspace,
            ReconciliationApplyRequest(
                drift_id=context.drift_id,
                summary="Commit repair",
                entries=[
                    ReconciliationFile(
                        path="course.yaml", content=(workspace / "course.yaml").read_text()
                    )
                ],
            ),
        )
    assert _git_output(workspace, "show", "-s", "--format=%s", "HEAD").strip() == "Commit repair"

    monkeypatch.setattr(history, "_persist_committed_provenance", original)
    write_valid_course(workspace, "Later external")
    assert read_current_state(workspace).drift == "drift"


def test_reconciliation_recovers_crash_between_ref_update_and_transaction_completion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    initialize_repository(workspace)
    write_valid_course(workspace, "Baseline")
    create_revision(workspace, RevisionCreateRequest(summary="Initial"))
    record_app_authored_state(workspace)
    repaired_content = (workspace / "course.yaml").read_text(encoding="utf-8")
    write_valid_course(workspace, "External")
    context = capture_reconciliation_context(workspace)
    original = history._write_transaction

    def fail_completion(
        target: Path, phase: Literal["active", "completed"], snapshot: str, ref: str
    ) -> None:
        if phase == "completed":
            raise OSError("transaction completion failed")
        original(target, phase, snapshot, ref)

    monkeypatch.setattr(history, "_write_transaction", fail_completion)
    with pytest.raises(OSError, match="transaction completion failed"):
        apply_reconciliation(
            workspace,
            ReconciliationApplyRequest(
                drift_id=context.drift_id,
                summary="Repair external edit",
                entries=[ReconciliationFile(path="course.yaml", content=repaired_content)],
            ),
        )
    assert _git_output(workspace, "show", "-s", "--format=%s", "HEAD").strip() == (
        "Repair external edit"
    )

    monkeypatch.setattr(history, "_write_transaction", original)
    state = read_current_state(workspace)

    assert state.drift == "clean"
    recovered_plan = read_course_plan(workspace)
    assert recovered_plan is not None
    assert recovered_plan.title == "Baseline"
    assert not (workspace / ".git" / history.TRANSACTION_FILE).exists()
    assert not (workspace / ".git" / history.PROVENANCE_PENDING_FILE).exists()


def test_unknown_clean_or_dirty_state_can_be_explicitly_accepted(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    initialize_repository(workspace)
    write_valid_course(workspace, "Existing clean Course")
    create_revision(workspace, RevisionCreateRequest(summary="External history"))
    clean_unknown = read_current_state(workspace)

    accepted_clean = accept_workspace_drift(
        workspace,
        DriftAcceptRequest(
            summary="Adopt existing Course history", drift_id=clean_unknown.drift_id or ""
        ),
    )

    imported = tmp_path / "imported"
    imported.mkdir()
    initialize_repository(imported)
    write_valid_course(imported, "Imported")
    dirty_unknown = read_current_state(imported)
    accepted_dirty = accept_workspace_drift(
        imported,
        DriftAcceptRequest(summary="Accept import", drift_id=dirty_unknown.drift_id or ""),
    )

    assert accepted_clean.id in {item.id for item in list_revisions(workspace)}
    assert accepted_dirty.id in {item.id for item in list_revisions(imported)}
    assert read_current_state(workspace).drift == "clean"
    assert read_current_state(imported).drift == "clean"


def test_provenance_includes_mode_and_rejects_unsafe_records(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    initialize_repository(workspace)
    write_valid_course(workspace)
    record_app_authored_state(workspace)
    os.chmod(workspace / "course.yaml", 0o755)
    assert read_current_state(workspace).drift == "drift"

    record = workspace / ".git" / history.PROVENANCE_FILE
    record.write_text("{}", encoding="ascii")
    with pytest.raises(WorkspaceHistoryError):
        read_current_state(workspace)
    record.unlink()
    record.symlink_to(tmp_path / "outside")
    with pytest.raises(WorkspaceHistoryError):
        read_current_state(workspace)


def test_drift_acceptance_rejects_a_changed_reviewed_snapshot(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    initialize_repository(workspace)
    write_valid_course(workspace, "Baseline")
    create_revision(workspace, RevisionCreateRequest(summary="Initial"))
    record_app_authored_state(workspace)
    write_valid_course(workspace, "First external edit")
    reviewed_id = read_current_state(workspace).drift_id
    write_valid_course(workspace, "Changed after review")

    with pytest.raises(WorkspaceDriftChangedError, match="review it again"):
        accept_workspace_drift(
            workspace,
            DriftAcceptRequest(summary="Stale acceptance", drift_id=reviewed_id or ""),
        )

    assert _git_output(workspace, "show", "-s", "--format=%s", "HEAD").strip() == "Initial"


def test_drift_acceptance_never_commits_changes_arriving_during_acceptance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    initialize_repository(workspace)
    write_valid_course(workspace, "Baseline")
    create_revision(workspace, RevisionCreateRequest(summary="Initial"))
    record_app_authored_state(workspace)
    write_valid_course(workspace, "Reviewed external edit")
    reviewed_id = read_current_state(workspace).drift_id
    original = history._capture_canonical_blobs
    calls = 0

    def change_after_reviewed_capture(path: Path) -> dict[str, history._CapturedEntry]:
        nonlocal calls
        calls += 1
        captured = original(path)
        if calls == 1:
            write_valid_course(workspace, "Concurrent external edit")
        return captured

    monkeypatch.setattr(history, "_capture_canonical_blobs", change_after_reviewed_capture)
    with pytest.raises(ValueError, match="changed while the Course Revision"):
        accept_workspace_drift(
            workspace,
            DriftAcceptRequest(summary="Accept reviewed edit", drift_id=reviewed_id or ""),
        )

    assert _git_output(workspace, "show", "-s", "--format=%s", "HEAD").strip() == "Initial"
    concurrent_plan = read_course_plan(workspace)
    assert concurrent_plan is not None
    assert concurrent_plan.title == "Concurrent external edit"


@pytest.mark.anyio
async def test_unproven_mode_change_is_drift_and_blocks_authoring(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    initialize_repository(workspace)
    write_valid_course(workspace, "Baseline")
    revision = create_revision(workspace, RevisionCreateRequest(summary="Initial"))
    (workspace / "course.yaml").chmod(0o755)

    transport = httpx2.ASGITransport(app=create_app(workspace))
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        state = await client.get("/api/workspace/current-state")
        blocked = await client.post(
            "/api/workspace/revisions", json={"summary": "Silently trust mode"}
        )

    assert state.status_code == 200
    assert state.json()["drift"] == "unknown"
    assert state.json()["changes"][0]["path"] == "course.yaml"
    assert state.json()["changes"][0]["status"] == "modified"
    assert blocked.status_code == 409
    assert [item.id for item in list_revisions(workspace)] == [revision.id]


@pytest.mark.anyio
async def test_opening_an_existing_clean_repository_does_not_seed_provenance(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    initialize_repository(workspace)
    write_valid_course(workspace, "Imported Course")
    create_revision(workspace, RevisionCreateRequest(summary="External initial history"))

    transport = httpx2.ASGITransport(app=create_app(workspace))
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/workspace/current-state")
        blocked = await client.post(
            "/api/workspace/revisions", json={"summary": "Bypass provenance review"}
        )
        accepted = await client.post(
            "/api/workspace/drift/accept",
            json={
                "summary": "Adopt existing Course history",
                "drift_id": response.json()["drift_id"],
            },
        )
        after = await client.get("/api/workspace/current-state")

    assert response.status_code == 200
    assert response.json()["drift"] == "unknown"
    assert response.json()["drift_id"]
    assert blocked.status_code == 409
    assert accepted.status_code == 201
    assert after.json()["drift"] == "clean"
    assert (workspace / ".git" / history.PROVENANCE_FILE).is_file()


def test_provenance_write_failure_recovers_from_prepared_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    initialize_repository(workspace)
    write_valid_course(workspace, "Baseline")
    record_app_authored_state(workspace)
    write_valid_course(workspace, "External")
    original = history._write_blob

    def fail_provenance(parent: Path, path: str, content: bytes) -> None:
        if path == history.PROVENANCE_FILE:
            raise OSError("disk full")
        original(parent, path, content)

    monkeypatch.setattr(history, "_write_blob", fail_provenance)
    drift_id = read_current_state(workspace).drift_id
    with pytest.raises(OSError, match="disk full"):
        accept_workspace_drift(
            workspace, DriftAcceptRequest(summary="Accept external", drift_id=drift_id or "")
        )
    assert list_revisions(workspace)
    assert (workspace / ".git" / history.PROVENANCE_PENDING_FILE).exists()

    monkeypatch.setattr(history, "_write_blob", original)
    write_valid_course(workspace, "Later external edit")
    assert read_current_state(workspace).drift == "drift"
    assert not (workspace / ".git" / history.PROVENANCE_PENDING_FILE).exists()


def test_record_provenance_rejects_capture_race(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    initialize_repository(workspace)
    write_valid_course(workspace)
    original = history._capture_canonical_blobs
    calls = 0

    def unstable(path: Path) -> dict[str, history._CapturedEntry]:
        nonlocal calls
        calls += 1
        captured = original(path)
        if calls == 2:
            captured = dict(captured)
            captured["course.yaml"] = history._CapturedEntry(mode="100644", content=b"changed")
        return captured

    monkeypatch.setattr(history, "_capture_canonical_blobs", unstable)
    with pytest.raises(ValueError, match="changed while provenance"):
        record_app_authored_state(workspace)


def test_revisions_restore_and_selective_revert_preserve_real_index(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    initialize_repository(workspace)
    write_valid_course(workspace, "First")

    first = create_revision(workspace, RevisionCreateRequest(summary="Initial Course"))
    write_valid_course(workspace, "Second")
    (workspace / "notes.md").write_text("staged outside Course", encoding="utf-8")
    git(workspace, "add", "notes.md")
    index_before = (workspace / ".git" / "index").read_bytes()
    second = create_revision(workspace, RevisionCreateRequest(summary="Revise Course"))

    assert [revision.id for revision in list_revisions(workspace)][:2] == [second.id, first.id]
    assert (workspace / ".git" / "index").read_bytes() == index_before
    write_valid_course(workspace, "Working change")
    reverted = revert_current_path(workspace, SelectiveRevertRequest(path="course.yaml"))
    assert reverted.clean is True
    restored = restore_revision(workspace, first.id)
    assert restored.validation.valid is True
    assert "title: First" in (workspace / "course.yaml").read_text(encoding="utf-8")
    assert (workspace / "notes.md").read_text(encoding="utf-8") == "staged outside Course"


def test_invalid_current_state_can_revert_or_restore_a_valid_revision(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    initialize_repository(workspace)
    write_valid_course(workspace, "Valid")
    revision = create_revision(workspace, RevisionCreateRequest(summary="Initial Course"))
    (workspace / "course.yaml").write_text("not: [valid", encoding="utf-8")

    reverted = revert_current_path(workspace, SelectiveRevertRequest(path="course.yaml"))

    assert reverted.validation.valid is True
    (workspace / "course.yaml").write_text("not: [valid", encoding="utf-8")
    restored = restore_revision(workspace, revision.id)
    assert restored.validation.valid is True
    assert not _git_output(
        workspace, "for-each-ref", "--format=%(refname)", "refs/course-harness/recovery"
    )
    assert revision.id in {item.id for item in list_revisions(workspace)}


def test_history_mutations_reject_stale_ids_noops_and_control_summaries(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    initialize_repository(workspace)
    write_valid_course(workspace)
    create_revision(workspace, RevisionCreateRequest(summary="Initial Course"))

    with pytest.raises(ValueError, match="no changes"):
        create_revision(workspace, RevisionCreateRequest(summary="No-op"))
    with pytest.raises(ValueError, match="one line"):
        RevisionCreateRequest(summary="Bad\tSummary")
    with pytest.raises(ValueError, match="currently changed"):
        revert_current_path(workspace, SelectiveRevertRequest(path="../../outside"))
    with pytest.raises(ValueError, match="not found"):
        restore_revision(workspace, "revision-" + "0" * 40)


def test_restore_repairs_a_canonical_symlink_without_following_it(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    initialize_repository(workspace)
    write_valid_course(workspace, "Safe")
    revision = create_revision(workspace, RevisionCreateRequest(summary="Initial Course"))
    external = tmp_path / "outside.yaml"
    external.write_text("outside stays unchanged", encoding="utf-8")
    (workspace / "course.yaml").unlink()
    (workspace / "course.yaml").symlink_to(external)

    restored = restore_revision(workspace, revision.id)

    assert restored.validation.valid is True
    assert not (workspace / "course.yaml").is_symlink()
    assert external.read_text(encoding="utf-8") == "outside stays unchanged"


def test_revert_and_restore_preserve_historical_executable_modes(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    initialize_repository(workspace)
    write_valid_course(workspace, "Executable")
    course = workspace / "course.yaml"
    course.chmod(0o755)
    executable_revision = create_revision(
        workspace, RevisionCreateRequest(summary="Executable canonical file")
    )
    course.chmod(0o644)

    reverted = revert_current_path(workspace, SelectiveRevertRequest(path="course.yaml"))

    assert reverted.clean is True
    assert course.stat().st_mode & 0o777 == 0o755

    write_valid_course(workspace, "Later content")
    course.chmod(0o644)
    create_revision(workspace, RevisionCreateRequest(summary="Later regular file"))
    restored = restore_revision(workspace, executable_revision.id)

    assert restored.validation.valid is True
    assert course.stat().st_mode & 0o777 == 0o755


def test_failed_revert_restores_an_invalid_symlink_snapshot(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    initialize_repository(workspace)
    write_valid_course(workspace)
    (workspace / "sources.yaml").write_text("version: 1\nsources: []\n", encoding="utf-8")
    create_revision(workspace, RevisionCreateRequest(summary="Initial Course"))
    external = tmp_path / "outside.yaml"
    external.write_text("outside", encoding="utf-8")
    (workspace / "course.yaml").unlink()
    (workspace / "course.yaml").symlink_to(external)
    (workspace / "sources.yaml").write_text("not: [valid", encoding="utf-8")

    with pytest.raises(ValueError, match="leave Current State invalid"):
        revert_current_path(workspace, SelectiveRevertRequest(path="sources.yaml"))

    assert (workspace / "course.yaml").is_symlink()
    assert (workspace / "course.yaml").readlink() == external
    assert (workspace / "sources.yaml").read_text(encoding="utf-8") == "not: [valid"


def test_pending_and_completed_transaction_recovery_preserve_current_bytes(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    initialize_repository(workspace)
    write_valid_course(workspace, "Baseline")
    create_revision(workspace, RevisionCreateRequest(summary="Initial Course"))
    write_valid_course(workspace, "Before interrupted restore")
    ref, snapshot = history._begin_transaction(workspace)
    write_valid_course(workspace, "Partially restored")

    recovered = read_current_state(workspace)

    assert recovered.validation.valid is True
    assert "title: Partially restored" in (workspace / "course.yaml").read_text()
    assert not (workspace / ".git" / history.TRANSACTION_FILE).exists()

    ref, snapshot = history._begin_transaction(workspace)
    write_valid_course(workspace, "Completed restore")
    history._write_transaction(workspace, "completed", snapshot, ref)

    completed = read_current_state(workspace)

    assert completed.validation.valid is True
    assert "title: Completed restore" in (workspace / "course.yaml").read_text()
    assert not (workspace / ".git" / history.TRANSACTION_FILE).exists()


def test_abandoned_run_preserves_invalid_and_valid_partial_work_for_review(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    initialize_repository(workspace)
    write_valid_course(workspace, "Baseline")
    create_revision(workspace, RevisionCreateRequest(summary="Initial Course"))

    snapshot = history.begin_run_boundary(workspace)
    mark_run_mutation(workspace, snapshot)
    (workspace / "course.yaml").write_text("not: [valid", encoding="utf-8")
    history._ACTIVE_RUNS.clear()
    recovered = read_current_state(workspace)

    assert recovered.validation.valid is False
    assert (workspace / "course.yaml").read_text(encoding="utf-8") == "not: [valid"
    assert not (workspace / ".git" / history.RUN_BOUNDARY_FILE).exists()

    history.begin_run_boundary(workspace)
    write_valid_course(workspace, "Valid partial work")
    history._ACTIVE_RUNS.clear()
    preserved = read_current_state(workspace)

    assert preserved.validation.valid is True
    assert "title: Valid partial work" in (workspace / "course.yaml").read_text()
    assert not (workspace / ".git" / history.RUN_BOUNDARY_FILE).exists()


def test_abandoned_run_preserves_unattributed_invalid_external_drift(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    initialize_repository(workspace)
    write_valid_course(workspace, "Baseline")
    create_revision(workspace, RevisionCreateRequest(summary="Initial Course"))
    record_app_authored_state(workspace)

    history.begin_run_boundary(workspace)
    (workspace / "course.yaml").write_text("not: [valid", encoding="utf-8")
    history._ACTIVE_RUNS.clear()

    state = read_current_state(workspace)

    assert state.validation.valid is False
    assert state.drift == "drift"
    assert (workspace / "course.yaml").read_text(encoding="utf-8") == "not: [valid"
    assert not (workspace / ".git" / history.RUN_BOUNDARY_FILE).exists()


def test_run_checkpoint_preserves_external_invalid_drift_after_agent_work(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    initialize_repository(workspace)
    write_valid_course(workspace, "Baseline")
    create_revision(workspace, RevisionCreateRequest(summary="Initial Course"))
    record_app_authored_state(workspace)

    snapshot = begin_run_boundary(workspace)
    mark_run_mutation(workspace, snapshot)
    write_valid_course(workspace, "Known agent partial work")
    checkpoint_run_mutation(workspace, snapshot)
    (workspace / "course.yaml").write_text("not: [valid", encoding="utf-8")
    history._ACTIVE_RUNS.clear()

    state = read_current_state(workspace)

    assert state.validation.valid is False
    assert state.drift == "drift"
    assert (workspace / "course.yaml").read_text(encoding="utf-8") == "not: [valid"


def test_completed_run_and_transaction_release_their_recovery_refs(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    initialize_repository(workspace)
    write_valid_course(workspace, "Baseline")
    create_revision(workspace, RevisionCreateRequest(summary="Initial Course"))

    run_snapshot = begin_run_boundary(workspace)
    finish = history.finish_run_boundary(workspace, run_snapshot)
    assert finish.validation.valid is True

    write_valid_course(workspace, "Working change")
    reverted = revert_current_path(workspace, SelectiveRevertRequest(path="course.yaml"))
    assert reverted.validation.valid is True
    assert not _git_output(
        workspace, "for-each-ref", "--format=%(refname)", "refs/course-harness/recovery"
    )


@pytest.mark.anyio
async def test_course_api_surfaces_an_abandoned_invalid_run_on_restart(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    initialize_repository(workspace)
    write_valid_course(workspace, "Baseline")
    create_revision(workspace, RevisionCreateRequest(summary="Initial Course"))
    snapshot = begin_run_boundary(workspace)
    mark_run_mutation(workspace, snapshot)
    (workspace / "course.yaml").write_text("not: [valid", encoding="utf-8")
    abandon_run_boundary(workspace, snapshot)

    transport = httpx2.ASGITransport(app=create_app(workspace))
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/course")

    assert response.status_code == 422
    assert "course.yaml is invalid" in response.json()["detail"]
    assert not (workspace / ".git" / history.RUN_BOUNDARY_FILE).exists()


def test_revert_ignores_a_preplanted_legacy_temp_symlink(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    initialize_repository(workspace)
    write_valid_course(workspace, "Baseline")
    create_revision(workspace, RevisionCreateRequest(summary="Initial Course"))
    write_valid_course(workspace, "Changed")
    external = tmp_path / "outside.txt"
    external.write_text("outside", encoding="utf-8")
    (workspace / ".course.yaml.course-harness.tmp").symlink_to(external)

    revert_current_path(workspace, SelectiveRevertRequest(path="course.yaml"))

    assert external.read_text(encoding="utf-8") == "outside"


def test_revision_rejects_a_concurrent_change_without_advancing_main(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    initialize_repository(workspace)
    write_valid_course(workspace, "Baseline")
    first = create_revision(workspace, RevisionCreateRequest(summary="Initial Course"))
    write_valid_course(workspace, "Candidate")
    original_capture = history._capture_canonical_blobs
    captures = 0

    def racing_capture(path: Path):
        nonlocal captures
        captures += 1
        captured = original_capture(path)
        if captures == 2:
            write_valid_course(path, "External race")
        return captured

    monkeypatch.setattr(history, "_capture_canonical_blobs", racing_capture)

    with pytest.raises(ValueError, match="changed while"):
        create_revision(workspace, RevisionCreateRequest(summary="Raced Course"))

    assert _git_output(workspace, "rev-parse", "refs/heads/main").strip() == first.id.removeprefix(
        "revision-"
    )


def test_current_state_rejects_an_oversized_canonical_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    initialize_repository(workspace)
    write_valid_course(workspace)
    monkeypatch.setattr(history, "MAX_CANONICAL_FILE_BYTES", 32)

    with pytest.raises(WorkspaceHistoryError, match="too large to inspect"):
        read_current_state(workspace)


def test_malformed_visible_history_is_reported_as_unsafe(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    initialize_repository(workspace)
    write_valid_course(workspace)
    create_revision(workspace, RevisionCreateRequest(summary="Initial Course"))
    git(
        workspace,
        "-c",
        "user.name=Hostile history",
        "-c",
        "user.email=hostile@example.invalid",
        "commit",
        "--allow-empty",
        "--allow-empty-message",
        "-m",
        "",
    )

    with pytest.raises(WorkspaceHistoryError, match="cannot be read safely"):
        list_revisions(workspace)


def test_revision_listing_bounds_hostile_commit_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    initialize_repository(workspace)
    write_valid_course(workspace)
    create_revision(workspace, RevisionCreateRequest(summary="Initial Course"))
    git(
        workspace,
        "-c",
        "user.name=Hostile history",
        "-c",
        "user.email=hostile@example.invalid",
        "commit",
        "--allow-empty",
        "-m",
        "x" * 256,
    )
    monkeypatch.setattr(history, "MAX_REVISION_METADATA_BYTES", 64)

    with pytest.raises(WorkspaceHistoryError):
        list_revisions(workspace)


def test_current_state_bounds_noncanonical_presentation_directory_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    initialize_repository(workspace)
    presentations = workspace / "presentations"
    presentations.mkdir()
    for index in range(3):
        (presentations / f"noise-{index}.txt").write_text("ignored", encoding="utf-8")
    monkeypatch.setattr(history, "MAX_PRESENTATIONS_DIRECTORY_ENTRIES", 2)

    with pytest.raises(WorkspaceHistoryError, match="presentations directory is too large"):
        read_current_state(workspace)


@pytest.mark.anyio
async def test_history_mutation_api_statuses_and_metadata_are_stable(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    transport = httpx2.ASGITransport(app=create_app(workspace))
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        absent = await client.post("/api/workspace/revisions", json={"summary": "Initial"})
        created_course = await client.post(
            "/api/course",
            json={
                "title": "Causal inference",
                "audience": "Researchers",
                "lectures": [{"title": "Confounding"}],
            },
        )
        created = await client.post("/api/workspace/revisions", json={"summary": "Initial Course"})
        listed = await client.get("/api/workspace/revisions")
        noop = await client.post("/api/workspace/revisions", json={"summary": "No-op"})
        stale_path = await client.post(
            "/api/workspace/current-state/revert", json={"path": "../../outside"}
        )
        stale_revision = await client.post(
            "/api/workspace/revisions/revision-" + "0" * 40 + "/restore"
        )
        write_valid_course(workspace, "Second Course")
        drift_id = read_current_state(workspace).drift_id
        second_revision = await client.post(
            "/api/workspace/drift/accept",
            json={"summary": "Revise Course", "drift_id": drift_id},
        )
        write_valid_course(workspace, "Uncommitted Course")
        reverted = await client.post(
            "/api/workspace/current-state/revert", json={"path": "course.yaml"}
        )
        restored = await client.post(f"/api/workspace/revisions/{created.json()['id']}/restore")

    assert absent.status_code == 404
    assert created_course.status_code == 201
    assert created.status_code == 201
    assert listed.status_code == 200
    assert listed.json()[0] == created.json()
    assert noop.status_code == 422
    assert stale_path.status_code == 422
    assert stale_revision.status_code == 422
    assert second_revision.status_code == 201
    assert reverted.status_code == 200
    assert reverted.json()["clean"] is True
    assert reverted.json()["drift"] == "clean"
    assert restored.status_code == 200
    assert restored.json()["validation"]["valid"] is True
    assert restored.json()["drift"] == "clean"


@pytest.mark.anyio
async def test_canonical_api_mutation_requires_drift_acceptance(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    initialize_repository(workspace)
    write_valid_course(workspace, "Baseline")
    create_revision(workspace, RevisionCreateRequest(summary="Initial Course"))
    record_app_authored_state(workspace)
    plan = read_course_plan(workspace)
    assert plan is not None
    write_course_plan(workspace, plan.model_copy(update={"title": "External edit"}))
    lecture_id = plan.lectures[0].id
    drift_id = read_current_state(workspace).drift_id

    transport = httpx2.ASGITransport(app=create_app(workspace))
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        blocked = await client.patch(
            f"/api/course/lectures/{lecture_id}", json={"title": "App edit"}
        )
        accepted = await client.post(
            "/api/workspace/drift/accept",
            json={"summary": "Accept external Course edit", "drift_id": drift_id},
        )
        changed = await client.patch(
            f"/api/course/lectures/{lecture_id}", json={"title": "App edit"}
        )

    assert blocked.status_code == 409
    assert "Workspace Drift" in blocked.json()["detail"]
    assert accepted.status_code == 201
    assert changed.status_code == 200
    assert read_current_state(workspace).drift == "clean"


@pytest.mark.anyio
async def test_unresolved_drift_survives_application_restart(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    initialize_repository(workspace)
    write_valid_course(workspace, "Baseline")
    create_revision(workspace, RevisionCreateRequest(summary="Initial Course"))
    record_app_authored_state(workspace)
    plan = read_course_plan(workspace)
    assert plan is not None
    write_course_plan(workspace, plan.model_copy(update={"title": "External edit"}))

    first_transport = httpx2.ASGITransport(app=create_app(workspace))
    async with httpx2.AsyncClient(transport=first_transport, base_url="http://test") as client:
        observed = await client.get("/api/workspace/current-state")
    assert observed.status_code == 200
    assert observed.json()["drift"] == "drift"

    restarted_transport = httpx2.ASGITransport(app=create_app(workspace))
    async with httpx2.AsyncClient(transport=restarted_transport, base_url="http://test") as client:
        blocked = await client.patch(
            f"/api/course/lectures/{plan.lectures[0].id}", json={"title": "App edit"}
        )

    assert blocked.status_code == 409
    assert "Workspace Drift" in blocked.json()["detail"]


def _git_output(workspace: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments], cwd=workspace, check=True, capture_output=True, text=True
    ).stdout


def test_current_state_reports_tracked_modification_and_deletion(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    initialize_repository(workspace)
    write_valid_course(workspace)
    sources = workspace / "sources.yaml"
    sources.write_text("version: 1\nsources: []\n", encoding="utf-8")
    commit_all(workspace)

    write_valid_course(workspace, "Revised statistical learning")
    sources.unlink()

    state = read_current_state(workspace)

    assert state.clean is False
    assert [(entry.path, entry.status, entry.line_count) for entry in state.changes] == [
        ("course.yaml", "modified", 14),
        ("sources.yaml", "deleted", None),
    ]
    assert any(line.startswith("-title:") for line in state.changes[0].diff_lines)
    assert any(line.startswith("+title:") for line in state.changes[0].diff_lines)
    assert state.changes[0].diff_truncated is False
    assert state.validation.valid is True


def test_current_state_reports_untracked_canonical_files_and_invalid_state(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    initialize_repository(workspace)
    write_valid_course(workspace)
    (workspace / "sources.yaml").write_text("not: [valid", encoding="utf-8")
    presentations = workspace / "presentations"
    presentations.mkdir()
    (presentations / "presentation-123456abcdef.yaml").write_text(
        "schema_version: 1\nid: presentation-123456abcdef\nlecture_id: lecture-bad\nslides: []\n",
        encoding="utf-8",
    )

    state = read_current_state(workspace)

    assert [(entry.path, entry.status) for entry in state.changes] == [
        ("course.yaml", "added"),
        ("presentations/presentation-123456abcdef.yaml", "added"),
        ("sources.yaml", "added"),
    ]
    assert state.validation.valid is False
    assert any(
        finding.startswith("sources.yaml is invalid:") for finding in state.validation.findings
    )


def test_current_state_reports_canonical_symlinks_as_invalid_without_following_them(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    initialize_repository(workspace)
    (workspace / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
    (workspace / "ignored.txt").write_text("ignored", encoding="utf-8")
    (workspace / "notes.md").write_text("not canonical", encoding="utf-8")
    external = tmp_path / "outside-course.yaml"
    external.write_text("not canonical", encoding="utf-8")
    (workspace / "course.yaml").symlink_to(external)
    presentations = workspace / "presentations"
    presentations.mkdir()
    (presentations / "presentation-123456abcdef.yaml").symlink_to(external)
    (presentations / "not-a-presentation.yaml").write_text("ignored", encoding="utf-8")

    state = read_current_state(workspace)

    assert [(entry.path, entry.status, entry.line_count) for entry in state.changes] == [
        ("course.yaml", "added", None),
        ("presentations/not-a-presentation.yaml", "added", 1),
        ("presentations/presentation-123456abcdef.yaml", "added", None),
    ]
    assert state.validation.valid is False
    assert (
        "course.yaml is a symbolic link and is not canonical Course state"
        in state.validation.findings
    )
    assert "course.yaml is missing from established Course state" not in state.validation.findings
    assert any(
        finding.startswith("presentations/not-a-presentation.yaml is invalid:")
        for finding in state.validation.findings
    )
    assert (
        "presentations/presentation-123456abcdef.yaml is a symbolic link "
        "and is not canonical Course state"
    ) in state.validation.findings


def test_current_state_rejects_a_workspace_without_its_own_repository(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()

    with pytest.raises(WorkspaceHistoryNotInitializedError, match="history is not initialized"):
        read_current_state(workspace)


def test_historical_tree_reads_enforce_canonical_file_count_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    initialize_repository(workspace)
    write_valid_course(workspace)
    (workspace / "sources.yaml").write_text("version: 1\nsources: []\n", encoding="utf-8")
    revision = create_revision(workspace, RevisionCreateRequest(summary="Initial"))
    monkeypatch.setattr(history, "MAX_CANONICAL_ENTRIES", 1)

    with pytest.raises(WorkspaceHistoryError, match="too many files"):
        history._tree_blobs(workspace, revision.id.removeprefix("revision-"))


def test_historical_tree_reads_enforce_per_file_size_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    initialize_repository(workspace)
    write_valid_course(workspace)
    revision = create_revision(workspace, RevisionCreateRequest(summary="Initial"))
    monkeypatch.setattr(history, "MAX_CANONICAL_FILE_BYTES", 8)

    with pytest.raises(WorkspaceHistoryError, match="history file is too large"):
        history._tree_blobs(workspace, revision.id.removeprefix("revision-"))


@pytest.mark.anyio
async def test_current_state_api_reports_unborn_changes_and_non_repository(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    transport = httpx2.ASGITransport(app=create_app(workspace))
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        no_history = await client.get("/api/workspace/current-state")
        created = await client.post(
            "/api/course",
            json={
                "title": "Causal inference",
                "audience": "Researchers",
                "lectures": [{"title": "Confounding"}],
            },
        )
        state = await client.get("/api/workspace/current-state")

    assert no_history.status_code == 404
    assert no_history.json() == {"detail": "Course Workspace history is not initialized"}
    assert created.status_code == 201
    assert state.status_code == 200
    payload = state.json()
    assert payload["clean"] is False
    assert payload["changes"][0]["path"] == "course.yaml"
    assert payload["changes"][0]["status"] == "added"
    assert payload["changes"][0]["line_count"] == 14
    assert "+title: Causal inference" in payload["changes"][0]["diff_lines"]
    assert payload["validation"] == {"valid": True, "findings": []}


@pytest.mark.anyio
async def test_current_state_api_reports_a_non_file_canonical_path_as_invalid(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    initialize_repository(workspace)
    (workspace / "course.yaml").mkdir()
    transport = httpx2.ASGITransport(app=create_app(workspace))

    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/workspace/current-state")

    assert response.status_code == 200
    payload = response.json()
    assert payload["validation"]["valid"] is False
    assert any(
        "course.yaml must be a regular file" in item for item in payload["validation"]["findings"]
    )


def test_create_revision_rejects_external_bytes_that_arrive_after_authoring_guard(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    initialize_repository(workspace)
    write_valid_course(workspace, "Baseline")
    create_revision(workspace, RevisionCreateRequest(summary="Initial"))
    record_app_authored_state(workspace)
    write_valid_course(workspace, "External edit")

    with pytest.raises(ValueError, match="Workspace Drift"):
        create_revision(workspace, RevisionCreateRequest(summary="Should not be captured"))


def test_current_state_ignores_a_malicious_fsmonitor_configuration(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    initialize_repository(workspace)
    write_valid_course(workspace)
    commit_all(workspace)
    marker = tmp_path / "fsmonitor-ran"
    monitor = tmp_path / "malicious-fsmonitor"
    monitor.write_text(f"#!/bin/sh\ntouch {marker}\n", encoding="utf-8")
    monitor.chmod(0o700)
    git(workspace, "config", "core.fsmonitor", str(monitor))
    write_valid_course(workspace, "Changed title")

    state = read_current_state(workspace)

    assert state.changes[0].status == "modified"
    assert not marker.exists()


def test_current_state_marks_a_non_directory_presentations_path_invalid(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    initialize_repository(workspace)
    write_valid_course(workspace)
    (workspace / "presentations").write_text("not a directory", encoding="utf-8")

    state = read_current_state(workspace)

    assert state.validation.valid is False
    assert "presentations must be a directory when it exists" in state.validation.findings


def test_current_state_handles_non_utf8_presentation_filenames_without_crashing(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    initialize_repository(workspace)
    write_valid_course(workspace)
    presentations = workspace / "presentations"
    presentations.mkdir()
    descriptor = os.open(
        os.fsencode(presentations) + b"/\xff.yaml",
        os.O_WRONLY | os.O_CREAT,
        0o600,
    )
    os.close(descriptor)

    state = read_current_state(workspace)

    assert state.validation.valid is False
    assert "A Presentation filename is not valid UTF-8" in state.validation.findings


def test_current_state_rejects_a_linked_worktree_repository(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    initialize_repository(workspace)
    external_git_dir = tmp_path / "git-dir"
    move(workspace / ".git", external_git_dir)
    (workspace / ".git").write_text(f"gitdir: {external_git_dir}\n", encoding="utf-8")

    with pytest.raises(WorkspaceHistoryError, match="history cannot be read safely"):
        read_current_state(workspace)


@pytest.mark.parametrize("metadata_name", ["objects", "refs"])
def test_current_state_rejects_external_git_metadata_directories(
    tmp_path: Path,
    metadata_name: str,
) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    initialize_repository(workspace)
    metadata = workspace / ".git" / metadata_name
    external = tmp_path / f"external-{metadata_name}"
    move(metadata, external)
    metadata.symlink_to(external, target_is_directory=True)

    with pytest.raises(WorkspaceHistoryError, match="history cannot be read safely"):
        read_current_state(workspace)


@pytest.mark.parametrize("relative_path", [("objects", "pack-link"), ("refs", "heads", "link")])
def test_current_state_rejects_nested_git_metadata_symlinks(
    tmp_path: Path,
    relative_path: tuple[str, ...],
) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    initialize_repository(workspace)
    link = workspace / ".git"
    for component in relative_path:
        link /= component
    link.parent.mkdir(parents=True, exist_ok=True)
    external = tmp_path / "external-metadata"
    external.mkdir()
    link.symlink_to(external, target_is_directory=True)

    with pytest.raises(WorkspaceHistoryError, match="history cannot be read safely"):
        read_current_state(workspace)


def test_current_state_rejects_git_alternates(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    initialize_repository(workspace)
    alternates = workspace / ".git" / "objects" / "info" / "alternates"
    alternates.parent.mkdir(exist_ok=True)
    alternates.write_text("/outside/object-store\n", encoding="utf-8")

    with pytest.raises(WorkspaceHistoryError, match="history cannot be read safely"):
        read_current_state(workspace)


def test_current_state_ignores_replace_refs_when_reading_head(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    initialize_repository(workspace)
    write_valid_course(workspace, "Baseline")
    commit_all(workspace)
    baseline = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=workspace, check=True, capture_output=True, text=True
    ).stdout.strip()
    write_valid_course(workspace, "Replacement")
    commit_all(workspace)
    replacement = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=workspace, check=True, capture_output=True, text=True
    ).stdout.strip()
    git(workspace, "reset", "--hard", baseline)
    git(workspace, "replace", baseline, replacement)

    state = read_current_state(workspace)

    assert state.clean is True
    assert state.changes == []


def test_current_state_rejects_nested_git_metadata_in_presentations(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    initialize_repository(workspace)
    (workspace / "presentations" / ".git").mkdir(parents=True)

    with pytest.raises(WorkspaceHistoryError, match="history cannot be read safely"):
        read_current_state(workspace)


def test_current_state_sees_ignored_canonical_files_and_final_working_tree(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    initialize_repository(workspace)
    (workspace / ".gitignore").write_text("course.yaml\n", encoding="utf-8")
    write_valid_course(workspace)

    ignored = read_current_state(workspace)

    assert [(entry.path, entry.status) for entry in ignored.changes] == [("course.yaml", "added")]

    git(workspace, "add", "-f", "course.yaml")
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Course Harness tests",
            "-c",
            "user.email=course-harness-tests@example.invalid",
            "commit",
            "--quiet",
            "-m",
            "Initial Course",
        ],
        cwd=workspace,
        check=True,
    )
    original = (workspace / "course.yaml").read_text(encoding="utf-8")
    git(workspace, "rm", "course.yaml")
    (workspace / "course.yaml").write_text(original, encoding="utf-8")

    final_tree = read_current_state(workspace)

    assert final_tree.clean is True
    assert final_tree.changes == []


def test_current_state_marks_a_deleted_tracked_course_as_invalid(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    initialize_repository(workspace)
    write_valid_course(workspace)
    commit_all(workspace)
    (workspace / "course.yaml").unlink()

    state = read_current_state(workspace)

    assert [(entry.path, entry.status) for entry in state.changes] == [("course.yaml", "deleted")]
    assert state.validation.valid is False
    assert "course.yaml is missing from established Course state" in state.validation.findings


def test_current_state_does_not_call_a_malformed_or_symlinked_course_missing(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    initialize_repository(workspace)
    (workspace / "course.yaml").write_text("not: [yaml", encoding="utf-8")

    malformed = read_current_state(workspace)

    assert any(
        finding.startswith("course.yaml is invalid:") for finding in malformed.validation.findings
    )
    assert (
        "course.yaml is missing from established Course state" not in malformed.validation.findings
    )

    (workspace / "course.yaml").unlink()
    (workspace / "course.yaml").symlink_to(tmp_path / "missing-course.yaml")

    broken_link = read_current_state(workspace)

    assert (
        "course.yaml is a symbolic link and is not canonical Course state"
        in broken_link.validation.findings
    )
    assert (
        "course.yaml is missing from established Course state"
        not in broken_link.validation.findings
    )


def test_current_state_validates_presentation_file_and_course_relationships(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    initialize_repository(workspace)
    plan = create_course_plan(
        CoursePlanInput(
            title="Course",
            audience="Authors",
            lectures=[LectureInput(title="Lecture")],
        )
    )
    lecture = plan.lectures[0]
    presentation = Presentation(id="presentation-123456abcdef", lecture_id=lecture.id, slides=[])
    write_presentation(workspace, presentation)
    (workspace / "presentations" / f"{presentation.id}.yaml").rename(
        workspace / "presentations" / "wrong-name.yaml"
    )
    write_course_plan(
        workspace,
        plan.model_copy(
            update={"lectures": [lecture.model_copy(update={"presentation_id": presentation.id})]}
        ),
    )

    state = read_current_state(workspace)

    assert state.validation.valid is False
    assert (
        f"presentations/wrong-name.yaml filename does not match Presentation ID {presentation.id}"
    ) in state.validation.findings


def test_current_state_validates_orphan_duplicate_and_missing_presentations(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    initialize_repository(workspace)
    plan = create_course_plan(
        CoursePlanInput(
            title="Course",
            audience="Authors",
            lectures=[LectureInput(title="Lecture")],
        )
    )
    lecture = plan.lectures[0]
    missing_id = "presentation-aaaaaaaaaaaa"
    write_course_plan(
        workspace,
        plan.model_copy(
            update={"lectures": [lecture.model_copy(update={"presentation_id": missing_id})]}
        ),
    )
    duplicate = Presentation(id="presentation-bbbbbbbbbbbb", lecture_id=lecture.id, slides=[])
    write_presentation(workspace, duplicate)
    duplicate_path = workspace / "presentations" / f"{duplicate.id}.yaml"
    duplicate_copy = workspace / "presentations" / "duplicate.yaml"
    duplicate_copy.write_text(duplicate_path.read_text(encoding="utf-8"), encoding="utf-8")
    orphan = Presentation(id="presentation-cccccccccccc", lecture_id="lecture-missing", slides=[])
    write_presentation(workspace, orphan)

    state = read_current_state(workspace)

    assert state.validation.valid is False
    assert (
        f"Lecture {lecture.id} links to missing Presentation {missing_id}"
        in state.validation.findings
    )
    assert f"Presentation ID {duplicate.id} appears in multiple files" in state.validation.findings
    assert f"Lecture {lecture.id} has multiple Presentations" in state.validation.findings
    assert (
        f"Presentation {orphan.id} references missing Lecture lecture-missing"
        in state.validation.findings
    )


def test_current_state_validates_source_focus_references(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    initialize_repository(workspace)
    plan = create_course_plan(
        CoursePlanInput(
            title="Course",
            audience="Authors",
            lectures=[LectureInput(title="Lecture", source_focus=["source-123456abcdef"])],
        )
    )
    write_course_plan(workspace, plan)

    state = read_current_state(workspace)

    assert state.validation.valid is False
    assert (
        f"Lecture {plan.lectures[0].id} Source Focus references missing Source source-123456abcdef"
    ) in state.validation.findings


def test_current_state_validates_duplicate_sources_and_slide_citations(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    initialize_repository(workspace)
    plan = create_course_plan(
        CoursePlanInput(
            title="Course",
            audience="Authors",
            lectures=[LectureInput(title="Lecture")],
        )
    )
    lecture = plan.lectures[0]
    presentation = Presentation(
        id="presentation-123456abcdef",
        lecture_id=lecture.id,
        slides=[
            TitleSlide(
                id="slide-123456abcdef",
                citations=[
                    SlideCitation(
                        source_id="source-aaaaaaaaaaaa",
                        label="Missing source",
                    )
                ],
            )
        ],
    )
    write_presentation(workspace, presentation)
    write_course_plan(
        workspace,
        plan.model_copy(
            update={"lectures": [lecture.model_copy(update={"presentation_id": presentation.id})]}
        ),
    )
    (workspace / "sources.yaml").write_text(
        """version: 1
sources:
  - id: source-bbbbbbbbbbbb
    resource_id: resource-123456abcdef
    source_version_id: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
    label: First
    admitted_at: '2026-01-01T00:00:00+00:00'
  - id: source-bbbbbbbbbbbb
    resource_id: resource-123456abcdef
    source_version_id: bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
    label: Duplicate
    admitted_at: '2026-01-01T00:00:00+00:00'
""",
        encoding="utf-8",
    )

    state = read_current_state(workspace)

    assert state.validation.valid is False
    assert "Source ID source-bbbbbbbbbbbb appears more than once" in state.validation.findings
    assert (
        "Resource ID resource-123456abcdef is admitted more than once" in state.validation.findings
    )
    assert (
        "Slide slide-123456abcdef Citation references missing Source source-aaaaaaaaaaaa"
    ) in state.validation.findings
