from pathlib import Path

import pytest

from course_harness.template_profiles import (
    BUILTIN_DEFAULT_ID,
    BUILTIN_DEFAULT_PROFILE_NAME,
    TemplateLayoutMapping,
    TemplateProfile,
    TemplateProfileRegistry,
    TemplateProfileSummary,
    delete_profile,
    read_profile,
    read_registry,
    resolve_profile,
    write_profile,
    write_profile_version,
    write_registry,
)


def _make_test_profile(profile_id: str = "tpl-000000000001", version: int = 1) -> TemplateProfile:
    return TemplateProfile(
        id=profile_id,
        name="Test Template",
        version=version,
        template_filename="test.pptx",
        slide_width=12192000,
        slide_height=6858000,
        slide_count=5,
        layouts=[
            TemplateLayoutMapping(
                semantic_layout="title",
                template_layout_index=0,
                confidence=0.9,
                rationale="Layout 'Title Slide' matched title",
            ),
            TemplateLayoutMapping(
                semantic_layout="bullets",
                template_layout_index=1,
                confidence=0.8,
                rationale="Layout 'Title and Content' matched bullets",
            ),
        ],
    )


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    d = tmp_path / "templates-data"
    d.mkdir()
    return d


@pytest.fixture
def cache_dir(tmp_path: Path) -> Path:
    d = tmp_path / "templates-cache"
    d.mkdir()
    return d


class TestRegistry:
    def test_read_empty_registry(self, data_dir: Path) -> None:
        registry = read_registry(data_dir)
        assert registry.profiles == []

    def test_write_and_read_registry(self, data_dir: Path) -> None:
        summary = TemplateProfileSummary(
            id="tpl-000000000001", name="Test", version=1, slide_count=5, mapped_layouts=9
        )
        registry = TemplateProfileRegistry(profiles=[summary])
        from course_harness.template_profiles import write_registry

        write_registry(data_dir, registry)
        loaded = read_registry(data_dir)
        assert len(loaded.profiles) == 1
        assert loaded.profiles[0].id == "tpl-000000000001"

    def test_delete_removes_from_registry(self, data_dir: Path, cache_dir: Path) -> None:
        profile = _make_test_profile()
        write_profile(data_dir, profile)
        write_registry(
            data_dir,
            TemplateProfileRegistry(
                profiles=[
                    TemplateProfileSummary(
                        id=profile.id,
                        name=profile.name,
                        version=profile.version,
                        slide_count=profile.slide_count,
                        mapped_layouts=len(profile.layouts),
                    )
                ]
            ),
        )
        delete_profile(data_dir, cache_dir, profile.id)
        registry = read_registry(data_dir)
        assert len(registry.profiles) == 0


class TestProfileStorage:
    def test_write_and_read_profile(self, data_dir: Path) -> None:
        profile = _make_test_profile()
        write_profile(data_dir, profile)
        loaded = read_profile(data_dir, profile.id)
        assert loaded is not None
        assert loaded.id == profile.id
        assert loaded.name == profile.name
        assert loaded.version == 1

    def test_read_nonexistent_profile(self, data_dir: Path) -> None:
        assert read_profile(data_dir, "tpl-nonexistent") is None

    def test_write_profile_version(self, data_dir: Path) -> None:
        profile = _make_test_profile()
        write_profile_version(data_dir, profile)
        pd = __import__("course_harness.template_profiles", fromlist=["profile_dir"]).profile_dir(
            data_dir, profile.id
        )
        assert (pd / "profile-v1.yaml").exists()
        assert (pd / "profile.yaml").exists()

    def test_profile_versioned_writes_preserve_latest(self, data_dir: Path) -> None:
        profile = _make_test_profile(version=1)
        write_profile_version(data_dir, profile)
        profile2 = _make_test_profile(version=2)
        profile2.layouts[0].confidence = 0.99
        write_profile_version(data_dir, profile2)

        pd = __import__("course_harness.template_profiles", fromlist=["profile_dir"]).profile_dir(
            data_dir, profile.id
        )
        assert (pd / "profile-v1.yaml").exists()
        assert (pd / "profile-v2.yaml").exists()
        loaded = read_profile(data_dir, profile.id)
        assert loaded is not None
        assert loaded.version == 2
        assert loaded.layouts[0].confidence == 0.99


class TestBuiltinDefault:
    def test_builtin_default_has_all_layouts(self) -> None:
        profile = resolve_profile(Path("/nonexistent"), None)
        assert profile.id == BUILTIN_DEFAULT_ID
        assert profile.name == BUILTIN_DEFAULT_PROFILE_NAME
        assert len(profile.layouts) == 9
        layouts = {m.semantic_layout for m in profile.layouts}
        expected = {
            "title",
            "section",
            "bullets",
            "two_column",
            "big_statement",
            "closing",
            "code",
            "image",
            "quote",
        }
        assert layouts == expected

    def test_builtin_default_explicit_id(self, data_dir: Path) -> None:
        profile = resolve_profile(data_dir, BUILTIN_DEFAULT_ID)
        assert profile.id == BUILTIN_DEFAULT_ID

    def test_resolve_missing_profile_raises(self, data_dir: Path) -> None:
        with pytest.raises(ValueError, match="not found"):
            resolve_profile(data_dir, "tpl-nonexistent")


class TestDeleteProfile:
    def test_delete_removes_directory(self, data_dir: Path, cache_dir: Path) -> None:
        profile = _make_test_profile()
        write_profile(data_dir, profile)
        pd = __import__("course_harness.template_profiles", fromlist=["profile_dir"]).profile_dir(
            data_dir, profile.id
        )
        assert pd.exists()
        delete_profile(data_dir, cache_dir, profile.id)
        assert not pd.exists()
        assert read_profile(data_dir, profile.id) is None
