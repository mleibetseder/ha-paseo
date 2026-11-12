"""Client for the hass-bridge plugin REST API."""

from __future__ import annotations

from http import HTTPStatus
from typing import Any

import aiohttp

from .const import SCHEMA_VERSION

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=10)
# An answer waits for the plugin to message the agent (the plugin gives up after 25 s).
ANSWER_TIMEOUT = aiohttp.ClientTimeout(total=30)


# A resume asks usage-auto-resume once per job and waits for each answer (the plugin answers within 25 s).
RESUME_BASE_TIMEOUT = 30
RESUME_PER_JOB_TIMEOUT = 10
RESUME_MAX_TIMEOUT = 120


def resume_timeout(jobs: int) -> float:
    """Total seconds to wait for a resume of this many jobs: 30 s plus 10 s per job, at most 120 s."""
    return min(RESUME_MAX_TIMEOUT, RESUME_BASE_TIMEOUT + RESUME_PER_JOB_TIMEOUT * max(1, jobs))


class PaseoError(Exception):
    """Base error of the bridge client."""


class PaseoAuthError(PaseoError):
    """The bridge token was rejected."""


class PaseoConnectionError(PaseoError):
    """The plugin could not be reached or answered unexpectedly."""


class PaseoRejectedError(PaseoError):
    """The plugin refused a question answer; `status` and `code` come from its error body."""

    def __init__(self, status: int, code: str | None) -> None:
        """Keep the HTTP status and the plugin's error code."""
        super().__init__(f"Answer refused: HTTP {status} {code}")
        self.status = status
        self.code = code


class PaseoServerError(PaseoConnectionError):
    """The plugin answered 500: reachable but failed on its side. Services report it as plugin_error."""


class PaseoNoMatchError(PaseoError):
    """No waiting quota job matches the agent (404 no_matching_job)."""


class PaseoDaemonError(PaseoError):
    """The plugin could not reach usage-auto-resume: 503 daemon_unavailable or 502 rpc_failed."""

    def __init__(self, code: str, reason: str | None = None) -> None:
        """Keep the plugin's error code and, for 503, the daemon client's reason code."""
        super().__init__(code)
        self.code = code
        self.reason = reason


class PaseoSchemaError(PaseoError):
    """The plugin does not support this contract version."""


# Statuses the answer route uses for refusals (REST API contract).
ANSWER_REFUSALS = frozenset({400, 404, 409, 502, 503})


async def _error_body(response: aiohttp.ClientResponse) -> dict[str, Any]:
    """The JSON error body of a plugin answer, or an empty dict when it has none."""
    try:
        data = await response.json(content_type=None)
    except (aiohttp.ClientError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


class PaseoBridgeClient:
    """Talks to the plugin: health (no auth), whoami and register (bearer token)."""

    def __init__(self, session: aiohttp.ClientSession, base_url: str, token: str) -> None:
        """Store the session, the plugin base URL and the bridge token."""
        self._session = session
        self._base_url = base_url.rstrip("/")
        self._token = token

    async def _request(
        self,
        method: str,
        path: str,
        *,
        auth: bool,
        json: dict[str, Any] | None = None,
        refusals: bool = False,
        timeout: aiohttp.ClientTimeout = REQUEST_TIMEOUT,  # noqa: ASYNC109
    ) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self._token}"} if auth else {}
        try:
            async with self._session.request(
                method,
                f"{self._base_url}{path}",
                headers=headers,
                json=json,
                timeout=timeout,
            ) as response:
                if response.status == HTTPStatus.UNAUTHORIZED:
                    raise PaseoAuthError("The bridge token was rejected")
                if response.status == HTTPStatus.INTERNAL_SERVER_ERROR:
                    raise PaseoServerError("The plugin failed with HTTP 500")
                if refusals and response.status in ANSWER_REFUSALS:
                    code = (await _error_body(response)).get("error")
                    raise PaseoRejectedError(
                        response.status, code if isinstance(code, str) else None
                    )
                if response.status in (
                    HTTPStatus.NOT_FOUND,
                    HTTPStatus.BAD_GATEWAY,
                    HTTPStatus.SERVICE_UNAVAILABLE,
                ):
                    error = await _error_body(response)
                    code = error.get("error")
                    if response.status == HTTPStatus.NOT_FOUND and code == "no_matching_job":
                        raise PaseoNoMatchError("No waiting job matches")
                    if code in ("daemon_unavailable", "rpc_failed"):
                        reason = error.get("reason")
                        raise PaseoDaemonError(code, reason if isinstance(reason, str) else None)
                if response.status == HTTPStatus.CONFLICT:
                    raise PaseoSchemaError("Unsupported schema")
                if response.status != HTTPStatus.OK:
                    raise PaseoConnectionError(f"Unexpected HTTP {response.status}")
                data = await response.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            raise PaseoConnectionError("Cannot reach the plugin") from err
        if not isinstance(data, dict):
            raise PaseoConnectionError("Unexpected response")
        return data

    async def health(self) -> dict[str, Any]:
        """Return {ok, instanceId, version, schema, paired}."""
        data = await self._request("GET", "/v1/health", auth=False)
        if not isinstance(data.get("instanceId"), str):
            raise PaseoConnectionError("Not a hass-bridge plugin")
        return data

    async def whoami(self) -> dict[str, Any]:
        """Probe the token; return {ok, instanceId}."""
        return await self._request("GET", "/v1/whoami", auth=True)

    async def register(self, webhook_url: str, *, push: bool = True) -> dict[str, Any]:
        """Tell the plugin where to push; return {instanceId}.

        push=False only stores the URL (the webhook does not exist yet).
        """
        body: dict[str, Any] = {"webhookUrl": webhook_url, "schema": SCHEMA_VERSION}
        if not push:
            body["push"] = False
        return await self._request("POST", "/v1/register", auth=True, json=body)

    async def unregister(self) -> dict[str, Any]:
        """Tell the plugin to forget the webhook and stop pushing."""
        return await self._request("POST", "/v1/unregister", auth=True)

    async def answer_question(
        self,
        worktree: str,
        question_id: str,
        choice: str,
        words: str | None = None,
        fingerprint: str | None = None,
    ) -> dict[str, Any]:
        """Answer an open question; return the plugin's {ok, recorded, ...} body.

        Raises PaseoRejectedError for 400/404/409 (the input) and 502/503 (the agent could
        not be told, so nothing is recorded and the answer can be sent again), PaseoAuthError
        for 401.
        """
        body: dict[str, Any] = {
            "worktree": worktree,
            "questionId": question_id,
            "choice": choice,
        }
        if words:
            body["words"] = words
        if fingerprint:
            body["fingerprint"] = fingerprint
        return await self._request(
            "POST",
            "/v1/questions/answer",
            auth=True,
            json=body,
            refusals=True,
            timeout=ANSWER_TIMEOUT,
        )

    async def resume_quota(self, agent_id: str, jobs: int = 1) -> dict[str, Any]:
        """Ask usage-auto-resume to resume one agent, or all ("all"), now.

        Returns {sent, counts, results}: one result per job (sent, used_up, unverified,
        canceled, send_failed, busy, sending, changed, unavailable, rpc_failed,
        not_attempted). `jobs` is the number of waiting jobs and sets the timeout.
        Raises PaseoNoMatchError when no waiting job matches and PaseoDaemonError when
        the plugin cannot reach usage-auto-resume.
        """
        data = await self._request(
            "POST",
            "/v1/quota/resume",
            auth=True,
            json={"agentId": agent_id},
            timeout=aiohttp.ClientTimeout(total=resume_timeout(jobs)),
        )
        sent = data.get("sent")
        counts = data.get("counts")
        results = data.get("results")
        return {
            "sent": sent if isinstance(sent, int) and not isinstance(sent, bool) else 0,
            "counts": counts if isinstance(counts, dict) else {},
            "results": [r for r in results if isinstance(r, dict)]
            if isinstance(results, list)
            else [],
        }

    async def cancel_ci_run(self, run_id: str) -> dict[str, Any]:
        """Ask the Paseo CI plugin to drop a queued run or stop a running one (writes its <id>.cancel marker).

        Returns {ok, runId, dispatcherAlive}. Raises PaseoRejectedError for 400 invalid_request, 404 unknown_run and
        409 run_finished, and for 502/503; PaseoServerError for 500; PaseoAuthError for 401.
        """
        return await self._request(
            "POST",
            "/v1/ci/cancel",
            auth=True,
            json={"runId": run_id},
            refusals=True,
        )
