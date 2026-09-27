"""Detected Credentials: model provider credentials offered for explicit adoption.

Course Harness never uses a credential from the environment on its own. It reports the
standard provider variables and AWS profiles it can see, without their values, and the
Course Author adds one as a Provider Account with a single confirmation.
"""

from __future__ import annotations

import configparser
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from course_harness import bedrock
from course_harness.providers import (
    FIXED_BASE_URLS,
    CredentialSource,
    ProviderAccount,
    ProviderAccountRequest,
    ProviderKind,
    validate_provider_url,
)

DetectionId = Literal[
    "openai-environment",
    "anthropic-environment",
    "openrouter-environment",
    "bedrock-key-environment",
    "aws-environment",
    "aws-profile",
]
DEFAULT_AWS_REGION = "us-east-1"
MAX_AWS_PROFILES = 100


class AwsProfileOption(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    region: str | None = None


class DetectedCredential(BaseModel):
    """A credential Course Harness can see but does not use until it is added."""

    model_config = ConfigDict(extra="forbid")

    id: DetectionId
    kind: ProviderKind
    credential_source: CredentialSource
    name: str
    # Where the credential comes from, naming variables or files but never values.
    origin: str
    base_url: str | None = None
    region: str | None = None
    aws_profiles: list[AwsProfileOption] = Field(default_factory=list)
    expires_at: str | None = None


class DetectedCredentialAddRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=100)
    region: str | None = None
    aws_profile: str | None = None


class DetectedCredentialUnavailableError(LookupError):
    """The Detected Credential is no longer available."""


_ENVIRONMENT_ORIGIN = "in the environment Course Harness started from"


def detect_credentials(
    environ: Mapping[str, str], accounts: list[ProviderAccount]
) -> list[DetectedCredential]:
    """List credentials that have not already been added as a Provider Account."""
    added = {account.detected_from for account in accounts}
    added_profiles = {
        account.aws_profile for account in accounts if account.credential_source == "aws-profile"
    }
    region = _environment_region(environ)
    detected: list[DetectedCredential] = []

    if environ.get("OPENAI_API_KEY"):
        base_url = environ.get("OPENAI_BASE_URL", "").strip().rstrip("/")
        if not base_url or base_url == FIXED_BASE_URLS["openai"]:
            detected.append(
                DetectedCredential(
                    id="openai-environment",
                    kind="openai",
                    credential_source="stored-key",
                    name="OpenAI",
                    origin=f"OPENAI_API_KEY {_ENVIRONMENT_ORIGIN}",
                )
            )
        elif _valid_url(base_url):
            detected.append(
                DetectedCredential(
                    id="openai-environment",
                    kind="openai-compatible",
                    credential_source="stored-key",
                    name=urlsplit(base_url).hostname or "OpenAI-compatible",
                    origin=f"OPENAI_API_KEY and OPENAI_BASE_URL {_ENVIRONMENT_ORIGIN}",
                    base_url=base_url,
                )
            )
    anthropic_base_url = environ.get("ANTHROPIC_BASE_URL", "").strip().rstrip("/")
    if environ.get("ANTHROPIC_API_KEY") and anthropic_base_url in {
        "",
        FIXED_BASE_URLS["anthropic"],
    }:
        detected.append(
            DetectedCredential(
                id="anthropic-environment",
                kind="anthropic",
                credential_source="stored-key",
                name="Anthropic",
                origin=f"ANTHROPIC_API_KEY {_ENVIRONMENT_ORIGIN}",
            )
        )
    if environ.get("OPENROUTER_API_KEY"):
        detected.append(
            DetectedCredential(
                id="openrouter-environment",
                kind="openrouter",
                credential_source="stored-key",
                name="OpenRouter",
                origin=f"OPENROUTER_API_KEY {_ENVIRONMENT_ORIGIN}",
            )
        )
    if environ.get("AWS_BEARER_TOKEN_BEDROCK"):
        detected.append(
            DetectedCredential(
                id="bedrock-key-environment",
                kind="bedrock",
                credential_source="stored-key",
                name="Amazon Bedrock",
                origin=f"AWS_BEARER_TOKEN_BEDROCK {_ENVIRONMENT_ORIGIN}",
                region=region,
            )
        )
    if environ.get("AWS_ACCESS_KEY_ID") and environ.get("AWS_SECRET_ACCESS_KEY"):
        label = environ.get("AWSUME_PROFILE") or environ.get("AWS_PROFILE")
        tool = "awsume" if environ.get("AWSUME_PROFILE") else None
        detected.append(
            DetectedCredential(
                id="aws-environment",
                kind="bedrock",
                credential_source="aws-environment",
                name=f"Amazon Bedrock ({label})" if label else "Amazon Bedrock",
                origin=(
                    f"AWS session credentials {_ENVIRONMENT_ORIGIN}"
                    + (f" ({tool}: {label})" if tool else f" ({label})" if label else "")
                ),
                region=region,
                expires_at=(
                    environ.get("AWS_CREDENTIAL_EXPIRATION")
                    or environ.get("AWSUME_EXPIRATION")
                    or None
                ),
            )
        )
    profiles = [
        profile for profile in read_aws_profiles(environ) if profile.name not in added_profiles
    ]
    if profiles:
        files = " and ".join(str(path) for path in _aws_config_paths(environ))
        detected.append(
            DetectedCredential(
                id="aws-profile",
                kind="bedrock",
                credential_source="aws-profile",
                name="Amazon Bedrock",
                origin=f"AWS profiles in {files}",
                region=region,
                aws_profiles=profiles,
            )
        )
    return [item for item in detected if item.id == "aws-profile" or item.id not in added]


def detected_account_request(
    environ: Mapping[str, str],
    accounts: list[ProviderAccount],
    detection_id: str,
    request: DetectedCredentialAddRequest,
) -> ProviderAccountRequest:
    """Build the Provider Account a Detected Credential becomes, reading its secret now."""
    detected = next(
        (item for item in detect_credentials(environ, accounts) if item.id == detection_id),
        None,
    )
    if detected is None:
        raise DetectedCredentialUnavailableError(detection_id)
    if detected.kind == "bedrock":
        region = request.region or detected.region or DEFAULT_AWS_REGION
        if detected.credential_source == "aws-profile":
            profile = next(
                (item for item in detected.aws_profiles if item.name == request.aws_profile),
                None,
            )
            if profile is None:
                raise DetectedCredentialUnavailableError(detection_id)
            return ProviderAccountRequest(
                name=request.name,
                kind="bedrock",
                credential_source="aws-profile",
                aws_profile=profile.name,
                region=request.region or profile.region or region,
            )
        if detected.credential_source == "aws-environment":
            return ProviderAccountRequest(
                name=request.name,
                kind="bedrock",
                credential_source="aws-environment",
                region=region,
            )
        return ProviderAccountRequest(
            name=request.name,
            kind="bedrock",
            api_key=SecretStr(environ["AWS_BEARER_TOKEN_BEDROCK"]),
            region=region,
        )
    variable = {
        "openai": "OPENAI_API_KEY",
        "openai-compatible": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
    }[detected.kind]
    return ProviderAccountRequest(
        name=request.name,
        kind=detected.kind,
        api_key=SecretStr(environ[variable]),
        base_url=detected.base_url,
    )


def read_aws_profiles(environ: Mapping[str, str]) -> list[AwsProfileOption]:
    """Profile names and regions from the shared AWS configuration files; never secrets."""
    config_path, credentials_path = _aws_config_paths(environ)
    regions: dict[str, str | None] = {}
    config = _read_ini(config_path)
    for section in config.sections():
        name = (
            section
            if section == "default"
            else section.removeprefix("profile ").strip()
            if section.startswith("profile ")
            else None
        )
        if name:
            regions[name] = config.get(section, "region", fallback=None)
    for section in _read_ini(credentials_path).sections():
        regions.setdefault(section, None)
    return [
        AwsProfileOption(
            name=name,
            region=region if region and bedrock.AWS_REGION_PATTERN.fullmatch(region) else None,
        )
        for name, region in sorted(regions.items())
        if bedrock.AWS_PROFILE_PATTERN.fullmatch(name)
    ][:MAX_AWS_PROFILES]


def _aws_config_paths(environ: Mapping[str, str]) -> tuple[Path, Path]:
    home = Path(environ.get("HOME") or os.path.expanduser("~"))
    return (
        Path(environ.get("AWS_CONFIG_FILE") or home / ".aws" / "config").expanduser(),
        Path(
            environ.get("AWS_SHARED_CREDENTIALS_FILE") or home / ".aws" / "credentials"
        ).expanduser(),
    )


def _read_ini(path: Path) -> configparser.RawConfigParser:
    parser = configparser.RawConfigParser(interpolation=None, strict=False)
    try:
        parser.read(path, encoding="utf-8")
    except configparser.Error, OSError, UnicodeDecodeError:
        return configparser.RawConfigParser()
    return parser


def _environment_region(environ: Mapping[str, str]) -> str | None:
    region = environ.get("AWS_REGION") or environ.get("AWS_DEFAULT_REGION")
    return region if region and bedrock.AWS_REGION_PATTERN.fullmatch(region) else None


def _valid_url(value: str) -> bool:
    try:
        validate_provider_url(value)
    except ValueError:
        return False
    return True
