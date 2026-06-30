# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""ACL REST API router.

Endpoints:

*   ``PUT  /api/v1/acl/set``     — Create or update ACL
*   ``GET  /api/v1/acl/get``     — Read ACL
*   ``DELETE /api/v1/acl/remove`` — Remove ACL (revert to inherited)
*   ``GET  /api/v1/acl/list``    — List shared URIs for a user
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from openviking.acl.store import (
    cache_acl,
    get_acl,
    get_effective_acl_async,
    get_acl_async,
    set_acl_async,
    delete_acl_async,
    invalidate_acl,
    invalidate_acl_tree,
)
from openviking.core.path_variables import resolve_path_variables
from openviking.server.auth import get_request_context
from openviking.server.dependencies import get_service
from openviking.server.identity import RequestContext
from openviking.server.models import Response

router = APIRouter(prefix="/api/v1/acl", tags=["acl"])


class SetAclRequest(BaseModel):
    uri: str
    shared: bool = False
    search_disabled: bool = False


@router.put("/set")
async def acl_set(
    request: SetAclRequest,
    _ctx: RequestContext = Depends(get_request_context),
):
    """Set ACL for a file or directory.

    Only the owner (resolved from the URI's user scope) may modify ACL.
    Setting ACL on a directory affects all descendants that do not have
    their own ``.acl.json`` override.
    """
    service = get_service()
    uri = resolve_path_variables(request.uri)

    # Resolve the owner from the URI path.
    # URI format: viking://user/{owner_id}/...
    parts = [p for p in uri[len("viking://"):].strip("/").split("/") if p]
    if len(parts) < 2 or parts[0] != "user":
        return Response(
            status="error",
            result=None,
            error={
                "code": "INVALID_URI",
                "message": "ACL only supported for URIs under viking://user/",
            },
        ).model_dump(exclude_none=True)

    owner = parts[1]

    # Only the owner or admin can set ACL
    if _ctx.user.user_id != owner and _ctx.role != "admin":
        return Response(
            status="error",
            result=None,
            error={
                "code": "PERMISSION_DENIED",
                "message": f"Only the owner ({owner}) or admin can modify ACL",
                "details": {"resource": uri},
            },
        ).model_dump(exclude_none=True)

    agfs = service.fs._async_agfs   # Direct AGFS access for store operations

    # State validation: search_disabled takes precedence.
    # When search_disabled is true, shared is forced to false
    # (the file is removed from search, only owner can access).
    effective_shared = request.shared
    effective_search_disabled = request.search_disabled
    if effective_search_disabled:
        effective_shared = False

    acl = {
        "owner": owner,
        "shared": effective_shared,
        "search_disabled": effective_search_disabled,
    }

    await set_acl_async(agfs, uri, acl)

    # Update synchronous cache for the namespace hook.
    cache_acl(uri, acl)
    # Invalidate children cache (inheritance may have changed).
    invalidate_acl_tree(uri)

    return Response(status="ok", result=acl).model_dump(exclude_none=True)


@router.get("/get")
async def acl_get(
    uri: str = Query(..., description="Viking URI"),
    _ctx: RequestContext = Depends(get_request_context),
):
    """Get effective ACL for a URI (includes inherited)."""
    service = get_service()
    resolved_uri = resolve_path_variables(uri)
    agfs = service.fs._async_agfs

    acl = await get_effective_acl_async(agfs, resolved_uri)
    return Response(status="ok", result=acl or {}).model_dump(exclude_none=True)


@router.delete("/remove")
async def acl_remove(
    uri: str = Query(..., description="Viking URI"),
    _ctx: RequestContext = Depends(get_request_context),
):
    """Remove ACL for a URI (revert to inherited or default)."""
    service = get_service()
    resolved_uri = resolve_path_variables(uri)
    agfs = service.fs._async_agfs

    # Verify owner
    parts = [p for p in resolved_uri[len("viking://"):].strip("/").split("/") if p]
    if len(parts) < 2:
        return Response(
            status="error",
            result=None,
            error={"code": "INVALID_URI", "message": "Invalid URI"},
        ).model_dump(exclude_none=True)

    owner = parts[1]
    if _ctx.user.user_id != owner and _ctx.role != "admin":
        return Response(
            status="error",
            result=None,
            error={
                "code": "PERMISSION_DENIED",
                "message": "Only the owner or admin can remove ACL",
            },
        ).model_dump(exclude_none=True)

    await delete_acl_async(agfs, resolved_uri)
    invalidate_acl(resolved_uri)
    invalidate_acl_tree(resolved_uri)

    return Response(status="ok", result={"removed": True}).model_dump(exclude_none=True)


@router.get("/list")
async def acl_list(
    owner: Optional[str] = Query(None, description="Filter by owner user_id"),
    _ctx: RequestContext = Depends(get_request_context),
):
    """List shared URIs.

    If *owner* is provided, lists that user's shared items.
    Otherwise lists the current user's shared items.
    """
    resolved_owner = owner or _ctx.user.user_id

    # ACL listing requires scanning the cache. For now, return cached entries.
    from openviking.acl.store import _acl_cache

    results = []
    for uri, acl in _acl_cache.items():
        if acl and acl.get("owner") == resolved_owner and acl.get("shared"):
            results.append({"uri": uri, **acl})

    return Response(status="ok", result=results).model_dump(exclude_none=True)
