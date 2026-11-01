from pathlib import Path

import pytest

from course_harness_classroom.repo_urls import (
    InvalidRepoUrl,
    check_resolves_publicly,
    validate_repo_url,
)


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/example/causal-inference",
        "https://gitlab.example.org/group/course.git",
        "https://git.example.org:8443/course.git",
    ],
)
def test_public_https_repositories_are_accepted(url: str) -> None:
    assert validate_repo_url(url) == url


@pytest.mark.parametrize(
    "url",
    [
        "",
        "http://github.com/example/course",
        "ssh://git@github.com/example/course",
        "git@github.com:example/course.git",
        "file:///srv/course.git",
        "/srv/course.git",
        "ext::sh -c touch% /tmp/pwned",
        "https://user:secret@github.com/example/course",
        "https://token@github.com/example/course",
        "https://github.com/example/course?ref=main",
        "https://github.com/example/course#main",
        "https://github.com",
        "https://github.com/",
        "https://localhost/course.git",
        "https://git.localhost/course.git",
        "https://printer.local/course.git",
        "https://127.0.0.1/course.git",
        "https://10.0.0.8/course.git",
        "https://192.168.1.4/course.git",
        "https://169.254.169.254/latest/meta-data",
        "https://[::1]/course.git",
        "https://[::ffff:127.0.0.1]/course.git",
        "https://0.0.0.0/course.git",
        "https://intranet/course.git",
        "-https://github.com/example/course",
        "https://github.com/example/my course",
    ],
)
def test_unsafe_or_non_https_addresses_are_refused(url: str) -> None:
    with pytest.raises(InvalidRepoUrl):
        validate_repo_url(url)


def test_the_development_setting_accepts_local_repositories(tmp_path: Path) -> None:
    assert validate_repo_url(str(tmp_path), allow_local=True) == str(tmp_path)
    assert validate_repo_url("file:///srv/course.git", allow_local=True)
    assert validate_repo_url("http://127.0.0.1:8080/course.git", allow_local=True)
    with pytest.raises(InvalidRepoUrl):
        validate_repo_url(str(tmp_path / "missing"), allow_local=True)
    with pytest.raises(InvalidRepoUrl):
        validate_repo_url("https://user:secret@127.0.0.1/course.git", allow_local=True)


def test_a_host_resolving_to_a_private_address_is_refused_before_fetching(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def resolve(*_arguments: object, **_keywords: object) -> list[tuple]:
        return [(2, 1, 6, "", ("10.1.2.3", 443))]

    monkeypatch.setattr("course_harness_classroom.repo_urls.socket.getaddrinfo", resolve)
    with pytest.raises(InvalidRepoUrl, match="non-public"):
        check_resolves_publicly("https://git.example.org/course.git")
