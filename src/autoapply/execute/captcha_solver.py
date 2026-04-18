"""Third-party captcha solver clients.

Used when AutoApply hits a blocking hCaptcha or reCAPTCHA v2 and the operator
has configured a paid solving service (2Captcha / CapMonster) via env:

    CAPTCHA_SOLVER          = "2captcha" | "capmonster" | ""
    CAPTCHA_SOLVER_API_KEY  = <provider api key>
    CAPTCHA_SOLVER_TIMEOUT  = <seconds, default 180>

The public entry point `solve_hcaptcha(site_key, page_url, ...)` returns the
solver-provided `h-captcha-response` token string, which the caller injects
into the page's hCaptcha response textarea(s) before clicking submit again.

Both providers use a two-step poll protocol:
    1. POST task payload → receive task id.
    2. Poll result endpoint every ~5s until solved or timeout.

Cost envelope: ~$1.50–$3 per 1000 hCaptcha solves (2025 pricing). A single
AutoApply run that hits 5 captchas costs ~$0.02, so economically the solver
is a backstop — the free hCaptcha accessibility cookie covers most cases.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx


log = logging.getLogger(__name__)


class SolverError(RuntimeError):
    """Raised when the solver API fails or times out."""


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def solve_hcaptcha(
    *,
    site_key: str,
    page_url: str,
    provider: str,
    api_key: str,
    timeout: int = 180,
) -> str:
    """Solve an hCaptcha via a paid third-party service.

    Returns the `h-captcha-response` token string on success.
    Raises SolverError on misconfiguration, API error, or timeout.
    """
    if not site_key:
        raise SolverError("site_key is empty — cannot request solve")
    if not page_url:
        raise SolverError("page_url is empty — cannot request solve")
    if not api_key:
        raise SolverError("CAPTCHA_SOLVER_API_KEY not set")

    provider = (provider or "").strip().lower()
    # NOTE: 2Captcha NO LONGER supports hCaptcha token solving — hCaptcha
    # has been dropped from their current API docs entirely (verified
    # 2026-04-18 against https://2captcha.com/api-docs). Callers that
    # detect hCaptcha on the page must route 2captcha through the Grid
    # path (captcha_coords.solve_hcaptcha_grid) instead, since that uses
    # their GridTask endpoint which remains fully supported.
    if provider == "2captcha":
        raise SolverError(
            "2captcha no longer supports hCaptcha token solving — route "
            "hCaptcha through the Grid path (solve_hcaptcha_grid) instead"
        )
    if provider == "capmonster":
        return _solve_via_capmonster(
            site_key=site_key, page_url=page_url, api_key=api_key, timeout=timeout
        )
    if provider == "capsolver":
        return _solve_via_capsolver(
            site_key=site_key, page_url=page_url, api_key=api_key, timeout=timeout
        )
    if provider == "anticaptcha":
        return _solve_via_anticaptcha(
            site_key=site_key, page_url=page_url, api_key=api_key, timeout=timeout
        )
    raise SolverError(f"unsupported CAPTCHA_SOLVER={provider!r}")


def _solve_via_anticaptcha(
    *, site_key: str, page_url: str, api_key: str, timeout: int
) -> str:
    """Anti-Captcha hCaptcha flow — docs: https://anti-captcha.com/apidoc

    Uses the same create/poll JSON protocol as CapSolver / CapMonster, with
    the host `api.anti-captcha.com` and task type `HCaptchaTaskProxyless`.
    Anti-Captcha supports hCaptcha out-of-the-box on all accounts (no tier
    gating), which 2Captcha notably does NOT.
    """
    deadline = time.time() + timeout
    with httpx.Client(timeout=30.0) as client:
        r = client.post(
            "https://api.anti-captcha.com/createTask",
            json={
                "clientKey": api_key,
                "task": {
                    "type": "HCaptchaTaskProxyless",
                    "websiteURL": page_url,
                    "websiteKey": site_key,
                },
            },
        )
        r.raise_for_status()
        body: dict[str, Any] = r.json()
        if body.get("errorId", -1) != 0:
            raise SolverError(
                f"anticaptcha create error: "
                f"{body.get('errorCode')} — {body.get('errorDescription')} ({body})"
            )
        task_id = body.get("taskId")
        if not task_id:
            raise SolverError(f"anticaptcha missing taskId: {body}")
        log.info("anticaptcha task submitted id=%s", task_id)

        poll = 3.0
        time.sleep(min(5.0, max(0, deadline - time.time())))
        while time.time() < deadline:
            resp = client.post(
                "https://api.anti-captcha.com/getTaskResult",
                json={"clientKey": api_key, "taskId": task_id},
            )
            resp.raise_for_status()
            rb: dict[str, Any] = resp.json()
            if rb.get("errorId", -1) != 0:
                raise SolverError(
                    f"anticaptcha poll error: "
                    f"{rb.get('errorCode')} — {rb.get('errorDescription')} ({rb})"
                )
            if rb.get("status") == "ready":
                sol = rb.get("solution") or {}
                token = sol.get("gRecaptchaResponse") or sol.get("token")
                if not token:
                    raise SolverError(f"anticaptcha missing solution token: {rb}")
                log.info("anticaptcha solved task=%s (len=%d)", task_id, len(token))
                return str(token)
            time.sleep(poll)
    raise SolverError(f"anticaptcha timed out after {timeout}s (task={task_id})")


# ---------------------------------------------------------------------------
# 2Captcha
# ---------------------------------------------------------------------------


def solve_recaptcha_v2_2captcha(
    *, site_key: str, page_url: str, api_key: str, timeout: int = 180
) -> str:
    """Solve Google reCAPTCHA v2 via 2Captcha's JSON API v2.

    Docs: https://2captcha.com/api-docs/recaptcha-v2

    Task type: `RecaptchaV2TaskProxyless`.
    Returns: g-recaptcha-response token (inject into
    `[name="g-recaptcha-response"]` textarea).
    """
    sol = _solve_via_2captcha_v2(
        task_type="RecaptchaV2TaskProxyless",
        task_params={"websiteURL": page_url, "websiteKey": site_key},
        api_key=api_key,
        timeout=timeout,
        solution_keys=("gRecaptchaResponse", "token"),
    )
    if not isinstance(sol, str) or not sol:
        raise SolverError(f"2captcha recaptcha_v2: empty token in solution")
    return sol


def solve_turnstile_2captcha(
    *,
    site_key: str,
    page_url: str,
    api_key: str,
    timeout: int = 180,
    action: str = "",
) -> str:
    """Solve Cloudflare Turnstile via 2Captcha's JSON API v2.

    Docs: https://2captcha.com/api-docs/cloudflare-turnstile

    Task type: `TurnstileTaskProxyless`.
    Returns: cf-turnstile-response token (inject into
    `[name="cf-turnstile-response"]` input).
    """
    task_params: dict[str, Any] = {"websiteURL": page_url, "websiteKey": site_key}
    if action:
        task_params["action"] = action
    sol = _solve_via_2captcha_v2(
        task_type="TurnstileTaskProxyless",
        task_params=task_params,
        api_key=api_key,
        timeout=timeout,
        solution_keys=("token", "gRecaptchaResponse"),
    )
    if not isinstance(sol, str) or not sol:
        raise SolverError(f"2captcha turnstile: empty token in solution")
    return sol


def solve_2captcha_task(
    *,
    task_type: str,
    task_params: dict[str, Any],
    api_key: str,
    timeout: int = 180,
    solution_keys: tuple[str, ...] = ("gRecaptchaResponse", "token"),
) -> Any:
    """Public entry point for any 2Captcha JSON v2 task type.

    Used by the grid (image-click) solver and any future captcha families
    (FunCaptcha, GeeTest, Turnstile variants, etc.). Callers supply the
    task type + params as documented at
    https://2captcha.com/api-docs and receive the raw solution object
    value (string for tokens, list for grid `click`, dict for coordinates).
    """
    return _solve_via_2captcha_v2(
        task_type=task_type,
        task_params=task_params,
        api_key=api_key,
        timeout=timeout,
        solution_keys=solution_keys,
    )


def _solve_via_2captcha_v2(
    *,
    task_type: str,
    task_params: dict[str, Any],
    api_key: str,
    timeout: int,
    solution_keys: tuple[str, ...],
) -> Any:
    """Shared JSON API v2 flow for 2Captcha (api.2captcha.com).

    1. POST https://api.2captcha.com/createTask  → taskId
    2. Poll POST https://api.2captcha.com/getTaskResult → solution

    Returns the first non-empty value from `solution[key]` for keys in
    `solution_keys` (in order). This lets the caller choose what they
    care about per task type — e.g. `"gRecaptchaResponse"` for token
    tasks, `"click"` for GridTask, `"coordinates"` for CoordinatesTask.

    Raises SolverError on any API-reported error or timeout.
    """
    deadline = time.time() + timeout
    with httpx.Client(timeout=30.0) as client:
        # Create task.
        create = client.post(
            "https://api.2captcha.com/createTask",
            json={
                "clientKey": api_key,
                "task": {"type": task_type, **task_params},
            },
        )
        create.raise_for_status()
        body = create.json()
        if body.get("errorId", -1) != 0:
            raise SolverError(
                f"2captcha {task_type} create: "
                f"{body.get('errorCode')} — {body.get('errorDescription')}"
            )
        task_id = body.get("taskId")
        if not task_id:
            raise SolverError(f"2captcha {task_type} missing taskId: {body}")
        log.info("2captcha %s task id=%s", task_type, task_id)

        # Poll — different task types solve at different speeds.
        #   GridTask / CoordinatesTask : ~10–30 s
        #   RecaptchaV2TaskProxyless    : ~15–40 s
        #   TurnstileTaskProxyless      : ~5–15 s
        first_wait = 5.0
        time.sleep(min(first_wait, max(0, deadline - time.time())))

        poll = 4.0
        while time.time() < deadline:
            result = client.post(
                "https://api.2captcha.com/getTaskResult",
                json={"clientKey": api_key, "taskId": task_id},
            )
            result.raise_for_status()
            rb = result.json()
            if rb.get("errorId", -1) != 0:
                raise SolverError(
                    f"2captcha {task_type} poll: "
                    f"{rb.get('errorCode')} — {rb.get('errorDescription')}"
                )
            if rb.get("status") == "ready":
                sol = rb.get("solution") or {}
                for k in solution_keys:
                    val = sol.get(k)
                    if val:
                        log.info(
                            "2captcha %s solved task=%s key=%s cost=$%s",
                            task_type, task_id, k, rb.get("cost", "?"),
                        )
                        return val
                raise SolverError(
                    f"2captcha {task_type} ready but no expected solution keys "
                    f"({solution_keys}) present in {sol}"
                )
            # status is "processing" — keep polling.
            time.sleep(poll)

    raise SolverError(
        f"2captcha {task_type} timed out after {timeout}s (task={task_id})"
    )


# ---------------------------------------------------------------------------
# CapMonster
# ---------------------------------------------------------------------------


def _solve_via_capsolver(
    *, site_key: str, page_url: str, api_key: str, timeout: int
) -> str:
    """CapSolver hCaptcha flow — docs: https://docs.capsolver.com/

    Same create/poll protocol as CapMonster but different host and the
    hCaptcha task type uses "ProxyLess" (capital L).

    1. POST https://api.capsolver.com/createTask
         { clientKey, task: { type: "HCaptchaTaskProxyLess",
                               websiteURL, websiteKey } }
       → { errorId: 0, taskId: "<uuid>" }
    2. Poll POST https://api.capsolver.com/getTaskResult
         { clientKey, taskId }
       → { errorId: 0, status: "ready",
           solution: { gRecaptchaResponse: "<token>" } }
    """
    deadline = time.time() + timeout
    with httpx.Client(timeout=30.0) as client:
        r = client.post(
            "https://api.capsolver.com/createTask",
            json={
                "clientKey": api_key,
                "task": {
                    "type": "HCaptchaTaskProxyLess",
                    "websiteURL": page_url,
                    "websiteKey": site_key,
                },
            },
        )
        r.raise_for_status()
        body: dict[str, Any] = r.json()
        if body.get("errorId", -1) != 0:
            raise SolverError(
                f"capsolver create error: "
                f"{body.get('errorCode')} — {body.get('errorDescription')} ({body})"
            )
        task_id = body.get("taskId")
        if not task_id:
            raise SolverError(f"capsolver missing taskId: {body}")
        log.info("capsolver task submitted id=%s", task_id)

        poll = 3.0
        # CapSolver's docs say "wait 1-10s" for the first poll.
        time.sleep(min(5.0, max(0, deadline - time.time())))
        while time.time() < deadline:
            resp = client.post(
                "https://api.capsolver.com/getTaskResult",
                json={"clientKey": api_key, "taskId": task_id},
            )
            resp.raise_for_status()
            rb: dict[str, Any] = resp.json()
            if rb.get("errorId", -1) != 0:
                raise SolverError(
                    f"capsolver poll error: "
                    f"{rb.get('errorCode')} — {rb.get('errorDescription')} ({rb})"
                )
            if rb.get("status") == "ready":
                sol = rb.get("solution") or {}
                token = sol.get("gRecaptchaResponse") or sol.get("token")
                if not token:
                    raise SolverError(f"capsolver missing solution token: {rb}")
                log.info("capsolver solved task=%s (len=%d)", task_id, len(token))
                return str(token)
            # status == "processing" → keep polling
            time.sleep(poll)
    raise SolverError(f"capsolver timed out after {timeout}s (task={task_id})")


def _solve_via_capmonster(
    *, site_key: str, page_url: str, api_key: str, timeout: int
) -> str:
    """CapMonster hCaptcha flow — docs: https://zennolab.atlassian.net/wiki/

    1. POST https://api.capmonster.cloud/createTask with:
         { clientKey, task: { type: "HCaptchaTaskProxyless",
                               websiteURL, websiteKey } }
       → { errorId: 0, taskId: <int> }
    2. Poll https://api.capmonster.cloud/getTaskResult every 5s:
       → { errorId: 0, status: "ready", solution: { gRecaptchaResponse } }
    """
    deadline = time.time() + timeout
    with httpx.Client(timeout=30.0) as client:
        r = client.post(
            "https://api.capmonster.cloud/createTask",
            json={
                "clientKey": api_key,
                "task": {
                    "type": "HCaptchaTaskProxyless",
                    "websiteURL": page_url,
                    "websiteKey": site_key,
                },
            },
        )
        r.raise_for_status()
        body: dict[str, Any] = r.json()
        if body.get("errorId", -1) != 0:
            raise SolverError(f"capmonster create error: {body}")
        task_id = body.get("taskId")
        if not task_id:
            raise SolverError(f"capmonster missing taskId: {body}")
        log.info("capmonster task submitted id=%s", task_id)

        poll = 5.0
        time.sleep(min(10.0, max(0, deadline - time.time())))
        while time.time() < deadline:
            resp = client.post(
                "https://api.capmonster.cloud/getTaskResult",
                json={"clientKey": api_key, "taskId": task_id},
            )
            resp.raise_for_status()
            rb: dict[str, Any] = resp.json()
            if rb.get("errorId", -1) != 0:
                raise SolverError(f"capmonster poll error: {rb}")
            if rb.get("status") == "ready":
                sol = rb.get("solution") or {}
                token = sol.get("gRecaptchaResponse") or sol.get("token")
                if not token:
                    raise SolverError(f"capmonster missing solution token: {rb}")
                log.info("capmonster solved task=%s (len=%d)", task_id, len(token))
                return str(token)
            # status == "processing" → keep polling
            time.sleep(poll)
    raise SolverError(f"capmonster timed out after {timeout}s (task={task_id})")
