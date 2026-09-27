"""Amazon Bedrock access through the AWS SDK.

A Bedrock Provider Account authenticates in one of three ways: a stored Bedrock API key,
an AWS profile that the AWS SDK resolves (and refreshes) on every use, or the AWS session
credentials in the environment Course Harness started from. AWS session credentials are
never copied into credential storage because they expire.

Unlike the other providers, Bedrock traffic goes through botocore rather than an isolated
httpx client, so it honours the standard AWS SDK proxy and CA bundle settings. Endpoint
overrides and an ambient ``AWS_BEARER_TOKEN_BEDROCK`` are disabled explicitly.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    import boto3
    from botocore.client import BaseClient

BedrockCredentialSource = Literal["stored-key", "aws-profile", "aws-environment"]
BedrockService = Literal["bedrock", "bedrock-runtime", "sts"]

AWS_REGION_PATTERN = re.compile(r"^[a-z]{2}(-[a-z]+)+-\d{1,2}$")
AWS_PROFILE_PATTERN = re.compile(r"^[A-Za-z0-9_.@+=,:/-]{1,128}$")

# Cross-region inference profile IDs prefix a foundation model ID with a geography.
_GEO_PREFIXES = frozenset({"us", "us-gov", "eu", "apac", "au", "ca", "jp", "global"})


class BedrockError(ValueError):
    """Bedrock could not be reached with the configured AWS access."""


def bedrock_endpoint(region: str) -> str:
    suffix = "amazonaws.com.cn" if region.startswith("cn-") else "amazonaws.com"
    return f"https://bedrock-runtime.{region}.{suffix}"


def foundation_model_id(model_id: str) -> str | None:
    """The foundation model behind a model ID or cross-region inference profile ID.

    ARNs, such as application inference profiles, cannot be resolved from the ID alone.
    """
    if model_id.startswith("arn:"):
        return None
    prefix, separator, rest = model_id.partition(".")
    if separator and prefix in _GEO_PREFIXES and "." in rest:
        return rest
    return model_id


# Only sizes that are the same in every region are listed; anything else is entered.
_CONTEXT_WINDOWS: tuple[tuple[str, int], ...] = (
    ("anthropic.claude", 200_000),
    ("amazon.nova-premier", 1_000_000),
    ("amazon.nova-pro", 300_000),
    ("amazon.nova-lite", 300_000),
    ("amazon.nova-micro", 128_000),
    ("meta.llama3-1", 128_000),
    ("meta.llama3-2", 128_000),
    ("meta.llama3-3", 128_000),
)


def known_context_window(model_id: str) -> int | None:
    base = foundation_model_id(model_id)
    if base is None:
        return None
    return next((size for prefix, size in _CONTEXT_WINDOWS if base.startswith(prefix)), None)


@dataclass(frozen=True)
class BedrockModelInfo:
    id: str
    vision: bool
    streaming: bool


@dataclass(frozen=True)
class BedrockAccess:
    """How one Bedrock Provider Account reaches AWS."""

    region: str
    credential_source: BedrockCredentialSource
    api_key: str | None = field(default=None, repr=False)
    aws_profile: str | None = None
    environ: Mapping[str, str] | None = field(default=None, repr=False, compare=False)

    def session(self) -> boto3.Session:
        import boto3
        from botocore.exceptions import BotoCoreError

        try:
            if self.credential_source == "stored-key":
                if not self.api_key:
                    raise BedrockError("The Bedrock API key is missing.")
                return boto3.Session(
                    botocore_session=_bearer_token_session(self.api_key),
                    region_name=self.region,
                )
            if self.credential_source == "aws-profile":
                # An explicit profile makes botocore ignore AWS credential variables.
                return boto3.Session(profile_name=self.aws_profile, region_name=self.region)
            environ = os.environ if self.environ is None else self.environ
            access_key = environ.get("AWS_ACCESS_KEY_ID")
            secret_key = environ.get("AWS_SECRET_ACCESS_KEY")
            if not access_key or not secret_key:
                raise BedrockError(
                    "The AWS session credentials this Provider Account uses are not in the "
                    "environment Course Harness was started from. Start Course Harness from a "
                    "shell with AWS credentials, or use an AWS profile instead."
                )
            return boto3.Session(
                aws_access_key_id=access_key,
                aws_secret_access_key=secret_key,
                aws_session_token=environ.get("AWS_SESSION_TOKEN") or None,
                region_name=self.region,
            )
        except BotoCoreError as error:
            raise BedrockError(describe_aws_error(error, self)) from error

    def client(
        self,
        service: BedrockService,
        *,
        read_timeout: float = 60,
        connect_timeout: float = 5,
    ) -> BaseClient:
        from botocore.config import Config
        from botocore.exceptions import BotoCoreError

        config = Config(
            read_timeout=read_timeout,
            connect_timeout=connect_timeout,
            retries={"mode": "standard", "max_attempts": 3},
            # Pinning the signature keeps an ambient AWS_BEARER_TOKEN_BEDROCK from
            # replacing SigV4 credentials.
            signature_version=(
                "bearer" if self.credential_source == "stored-key" and service != "sts" else "v4"
            ),
            ignore_configured_endpoint_urls=True,
        )
        try:
            return self.session().client(service, config=config)  # type: ignore[call-overload]
        except BotoCoreError as error:
            raise BedrockError(describe_aws_error(error, self)) from error


def _bearer_token_session(token: str) -> Any:
    """A botocore session that signs every request with one Bedrock API key."""
    from botocore.session import Session
    from botocore.tokens import FrozenAuthToken

    class BearerTokenSession(Session):
        def get_auth_token(self, **_kwargs: Any) -> FrozenAuthToken:
            return FrozenAuthToken(token)

        def get_credentials(self) -> None:  # type: ignore[override]
            return None

    return BearerTokenSession()


def describe_aws_error(error: Exception, access: BedrockAccess) -> str:
    """Explain an AWS SDK failure in terms of what the Course Author can do next."""
    from botocore.exceptions import (
        ClientError,
        EndpointConnectionError,
        NoCredentialsError,
        ProfileNotFound,
        SSOError,
        TokenRetrievalError,
        UnauthorizedSSOTokenError,
    )

    if isinstance(error, BedrockError):
        return str(error)
    if isinstance(error, ProfileNotFound):
        return f"The AWS profile {access.aws_profile!r} was not found in your AWS configuration."
    if isinstance(error, (SSOError, UnauthorizedSSOTokenError, TokenRetrievalError)):
        return (
            f"The AWS sign-in for profile {access.aws_profile!r} has expired or is missing. "
            f"Run `aws sso login --profile {access.aws_profile}` and try again."
        )
    if isinstance(error, NoCredentialsError):
        return "No AWS credentials were found for this Provider Account."
    if isinstance(error, EndpointConnectionError):
        return f"Course Harness could not reach Amazon Bedrock in {access.region}."
    if isinstance(error, ClientError):
        details = error.response.get("Error", {})
        code = str(details.get("Code", ""))
        message = str(details.get("Message", "")).strip()
        if code in {"ExpiredToken", "ExpiredTokenException"}:
            if access.credential_source == "aws-environment":
                return (
                    "The AWS session credentials have expired. Refresh them, for example by "
                    "running awsume again, and restart Course Harness from that shell."
                )
            return "The AWS credentials have expired. Refresh your AWS sign-in and try again."
        if code in {
            "UnrecognizedClientException",
            "InvalidSignatureException",
            "InvalidClientTokenId",
            "SignatureDoesNotMatch",
        }:
            return "AWS rejected the credentials. Check them and try again."
        return f"Amazon Bedrock refused the request: {message or code or 'unknown error'}"
    return f"Amazon Bedrock could not be reached: {error}"


def verify_access(access: BedrockAccess) -> None:
    """Check that the credentials authenticate, without needing access to any model."""
    try:
        if access.credential_source == "stored-key":
            # A Bedrock API key only works with Bedrock itself.
            access.client("bedrock").list_foundation_models(byOutputModality="TEXT")
        else:
            access.client("sts").get_caller_identity()
    except BedrockError:
        raise
    except Exception as error:
        raise BedrockError(describe_aws_error(error, access)) from error


def list_models(access: BedrockAccess, *, limit: int) -> list[BedrockModelInfo]:
    """Text models the account can call on demand, cross-region inference profiles first.

    Recent models only run through an inference profile, so its ID is what a Model Preset
    should use whenever one exists.
    """
    client = access.client("bedrock")
    try:
        summaries = client.list_foundation_models(byOutputModality="TEXT")["modelSummaries"]
    except Exception as error:
        raise BedrockError(describe_aws_error(error, access)) from error
    foundation: dict[str, dict[str, Any]] = {
        summary["modelId"]: summary
        for summary in summaries
        if isinstance(summary, dict)
        and isinstance(summary.get("modelId"), str)
        and summary.get("modelLifecycle", {}).get("status", "ACTIVE") == "ACTIVE"
    }

    def info(model_id: str, summary: Mapping[str, Any] | None) -> BedrockModelInfo:
        summary = summary or {}
        return BedrockModelInfo(
            id=model_id,
            vision="IMAGE" in summary.get("inputModalities", []),
            streaming=summary.get("responseStreamingSupported", True) is not False,
        )

    models: list[BedrockModelInfo] = []
    try:
        paginator = client.get_paginator("list_inference_profiles")
        for page in paginator.paginate(typeEquals="SYSTEM_DEFINED"):
            for profile in page.get("inferenceProfileSummaries", []):
                profile_id = profile.get("inferenceProfileId")
                if not isinstance(profile_id, str) or profile.get("status") != "ACTIVE":
                    continue
                base = foundation_model_id(profile_id)
                if base is not None and base in foundation:
                    models.append(info(profile_id, foundation[base]))
    except Exception:
        # Listing inference profiles needs its own permission; on-demand models remain.
        pass
    models.extend(
        info(model_id, summary)
        for model_id, summary in foundation.items()
        if "ON_DEMAND" in summary.get("inferenceTypesSupported", [])
    )
    return models[:limit]


def describe_model(access: BedrockAccess, model_id: str) -> BedrockModelInfo | None:
    """Foundation model metadata, when the ID names one and the account may read it."""
    base = foundation_model_id(model_id)
    if base is None:
        return None
    try:
        details = access.client("bedrock").get_foundation_model(modelIdentifier=base)
    except Exception:
        return None
    summary = details.get("modelDetails", {})
    return BedrockModelInfo(
        id=model_id,
        vision="IMAGE" in summary.get("inputModalities", []),
        streaming=summary.get("responseStreamingSupported", True) is not False,
    )


def probe_tool_calling(access: BedrockAccess, model_id: str) -> bool:
    """Ask the model for one tool call; this also proves the account may invoke it."""
    from botocore.exceptions import ClientError

    runtime = access.client("bedrock-runtime")
    try:
        response = runtime.converse(
            modelId=model_id,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"text": "Call add_numbers with a=1 and b=2. Return only the tool call."}
                    ],
                }
            ],
            toolConfig={
                "tools": [
                    {
                        "toolSpec": {
                            "name": "add_numbers",
                            "description": "Add two integers.",
                            "inputSchema": {
                                "json": {
                                    "type": "object",
                                    "properties": {
                                        "a": {"type": "integer"},
                                        "b": {"type": "integer"},
                                    },
                                    "required": ["a", "b"],
                                }
                            },
                        }
                    }
                ]
            },
            inferenceConfig={"maxTokens": 256},
        )
    except ClientError as error:
        details = error.response.get("Error", {})
        if (
            details.get("Code") == "ValidationException"
            and "tool" in str(details.get("Message", "")).lower()
        ):
            return False
        raise BedrockError(describe_aws_error(error, access)) from error
    except Exception as error:
        raise BedrockError(describe_aws_error(error, access)) from error
    content = response.get("output", {}).get("message", {}).get("content", [])
    return any(
        isinstance(block, dict) and block.get("toolUse", {}).get("name") == "add_numbers"
        for block in content
    )
