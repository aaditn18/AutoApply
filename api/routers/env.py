"""GET + PUT /api/env — .env editor with secret masking.

Read-side returns the parsed dict with secret values masked
(``****1234``). Write-side accepts a partial update and writes only
the provided keys; lines for keys not in the body are left untouched.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from api.schemas import EnvKey, EnvPayload, EnvWriteIn, WriteResult
from api.services import config_writer
from api.services.config_writer import (
    KNOWN_KEYS,
    SECRET_KEYS,
    mask_secret,
    read_env,
    write_env,
)


router = APIRouter(prefix="/api/env", tags=["env"])


@router.get("", response_model=EnvPayload)
def get_env() -> EnvPayload:
    raw = read_env()
    # Surface every KNOWN_KEY even if missing, so the UI can suggest
    # the full set.
    keys: list[EnvKey] = []
    seen: set[str] = set()
    for k in KNOWN_KEYS:
        v = raw.get(k, "")
        is_secret = k in SECRET_KEYS
        keys.append(
            EnvKey(
                key=k,
                value=mask_secret(v) if is_secret else v,
                is_secret=is_secret,
                is_set=k in raw,
            )
        )
        seen.add(k)
    # Append any unknown-but-set keys so we don't hide them.
    for k, v in raw.items():
        if k in seen:
            continue
        is_secret = k in SECRET_KEYS or k.endswith("_KEY") or k.endswith("_PAT")
        keys.append(
            EnvKey(
                key=k,
                value=mask_secret(v) if is_secret else v,
                is_secret=is_secret,
                is_set=True,
            )
        )

    return EnvPayload(keys=keys, raw_path=str(config_writer.ALLOWED_PATHS["env"]))


@router.put("", response_model=WriteResult)
def put_env(body: EnvWriteIn) -> WriteResult:
    if not body.updates:
        raise HTTPException(status_code=400, detail="updates is empty")
    try:
        backup = write_env(body.updates)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return WriteResult(
        ok=True,
        message=f"updated {len(body.updates)} key(s)",
        backup_path=str(backup) if backup else None,
    )
