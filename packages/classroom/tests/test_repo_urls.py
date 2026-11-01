import ipaddress
from pathlib import Path

import pytest

from course_harness_classroom.repo_urls import (
    InvalidRepoUrl,
    check_resolves_publicly,
    is_public_address,
    validate_repo_url,
)


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/example/causal-inference",
        "https://gitlab.example.org/group/course.git",
        "https://git.example.org:443/course.git",
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
        "https://git.example.org:8443/course.git",
        "https://git.example.org:22/course.git",
        "https://[64:ff9b::7f00:1]/course.git",
        "https://[64:ff9b::a00:1]/course.git",
        "https://[::7f00:1]/course.git",
        "https://[2002:7f00:1::]/course.git",
        "https://[2002:a9fe:a9fe::]/course.git",
        "https://[2001:0:4136:e378:8000:63bf:80ff:fffe]/course.git",
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


@pytest.mark.parametrize(
    ("address", "public"),
    [
        ("93.184.216.34", True),
        ("2606:4700::6810:84e5", True),
        ("64:ff9b::5db8:d822", True),  # NAT64 of 93.184.216.34
        ("64:ff9b::a00:1", False),  # NAT64 of 10.0.0.1
        ("::a00:1", False),  # IPv4-compatible 10.0.0.1
        ("::ffff:10.0.0.1", False),
        ("2002:5db8:d822::1", True),  # 6to4 of 93.184.216.34
        ("2002:c0a8:0101::1", False),  # 6to4 of 192.168.1.1
        # Teredo: server 65.54.227.120, client 127.0.0.1 (obfuscated as 7fff:fffe ^ ffff:ffff).
        ("2001:0:4136:e378:8000:63bf:80ff:fffe", False),
        ("::1", False),
        ("fe80::1", False),
        ("fc00::1", False),
    ],
)
def test_embedded_ipv4_addresses_are_checked_too(address: str, public: bool) -> None:
    assert is_public_address(ipaddress.ip_address(address)) is public


def test_the_development_setting_allows_other_ports() -> None:
    assert validate_repo_url("https://127.0.0.1:8443/course.git", allow_local=True)
