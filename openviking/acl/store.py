# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""ACL storage layer.

ACL records are stored as ``.acl.json`` files in AGFS alongside the
target file or directory. The storage format is a minimal JSON object::

    {
      "owner": "alice",
      "shared": false,
      "search_disabled": false
    }

Folder inheritance: if a file has no own ``.acl.json``, the effective
ACL is inherited from the nearest parent directory that has one.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from openviking.pyagfs.exceptions import AGFSNotFoundError

logger = logging.getLogger(__name__)

ACL_FILENAME = ".acl.json"


def _parent_uri(uri: str) -> Optional[str]:
    """Return the parent directory URI, or None if at root."""
    stripped = uri.rstrip("/")
    if stripped in ("viking://", "viking://user", "viking://resources"):
        return None
    idx = stripped.rfind("/")
    if idx < 0:
        return None
    return stripped[:idx]


async def _read_acl_from_agfs(agfs, path: str) -> Optional[Dict[str, Any]]:
    """Read an ACL record from AGFS. Returns None if not found."""
    try:
        raw = await agfs.read(path)
        content = raw if isinstance(raw, bytes) else raw.encode() if raw else b"{}"
        return json.loads(content) if content else None
    except (AGFSNotFoundError, Exception):
        return None


async def _write_acl_to_agfs(agfs, path: str, acl: Dict[str, Any]) -> None:
    """Write an ACL record to AGFS."""
    content = json.dumps(acl, ensure_ascii=False).encode("utf-8")
    await agfs.write(path, content)


async def get_effective_acl_async(agfs, uri: str) -> Optional[Dict[str, Any]]:
    """Resolve effective ACL by walking up the directory tree.

    Returns the first ACL found starting from *uri* and moving upward.
    Returns None if no ACL record exists anywhere in the path.
    """
    current = uri.rstrip("/") if uri.endswith("/") else uri

    while current:
        acl_path = f"{current}/{ACL_FILENAME}"
        acl = await _read_acl_from_agfs(agfs, acl_path)
        if acl is not None:
            return acl
        parent = _parent_uri(current)
        if parent is None or parent == current:
            break
        current = parent

    return None


async def get_acl_async(agfs, uri: str) -> Optional[Dict[str, Any]]:
    """Read ACL for a specific URI (no inheritance)."""
    acl_path = f"{uri.rstrip('/')}/{ACL_FILENAME}"
    return await _read_acl_from_agfs(agfs, acl_path)


async def set_acl_async(agfs, uri: str, acl: Dict[str, Any]) -> None:
    """Write ACL for a specific URI."""
    acl_path = f"{uri.rstrip('/')}/{ACL_FILENAME}"
    await _write_acl_to_agfs(agfs, acl_path, acl)


async def delete_acl_async(agfs, uri: str) -> None:
    """Remove ACL record for a specific URI (revert to inherited/default)."""
    acl_path = f"{uri.rstrip('/')}/{ACL_FILENAME}"
    try:
        await agfs.delete(acl_path)
    except AGFSNotFoundError:
        pass  # Already gone — idempotent


# ── Synchronous facade for the namespace hook ──

# The namespace.is_accessible() check runs synchronously. To avoid
# blocking, we maintain a simple in-memory cache of ACL records loaded
# from AGFS. The cache is invalidated when ACLs are modified via the API.

import threading

_acl_cache: Dict[str, Optional[Dict[str, Any]]] = {}
_cache_lock = threading.Lock()


def _cache_key(uri: str) -> str:
    return uri.rstrip("/")


def get_acl(uri: str) -> Optional[Dict[str, Any]]:
    """Synchronous ACL lookup with in-memory cache.

    Called from ``resolve_acl_access()`` in the namespace hook.
    Returns the nearest ACL record for *uri*, or None.
    """
    key = _cache_key(uri)
    with _cache_lock:
        if key in _acl_cache:
            return _acl_cache[key]

    # Cache miss — walk up the tree to find nearest cached parent.
    # This handles inheritance for files whose directory ancestor has
    # a cached ACL but the file URI itself was never explicitly cached.
    current = key
    while current:
        parent = _parent_uri(current)
        if parent is None or parent == current:
            break
        current = parent
        parent_key = _cache_key(current)
        with _cache_lock:
            if parent_key in _acl_cache and _acl_cache[parent_key] is not None:
                return _acl_cache[parent_key]

    return None


def cache_acl(uri: str, acl: Optional[Dict[str, Any]]) -> None:
    """Store ACL in synchronous cache. Called by the API layer."""
    with _cache_lock:
        _acl_cache[_cache_key(uri)] = acl
    logger.info("acl cache set: %s → shared=%s search_disabled=%s",
                 uri,
                 acl.get("shared") if acl else None,
                 acl.get("search_disabled") if acl else None)


def invalidate_acl(uri: str) -> None:
    """Remove ACL from cache. Called on ACL write or delete."""
    with _cache_lock:
        _acl_cache.pop(_cache_key(uri), None)


def invalidate_acl_tree(uri: str) -> None:
    """Invalidate all cached ACLs under a directory prefix."""
    prefix = _cache_key(uri)
    with _cache_lock:
        keys_to_remove = [k for k in _acl_cache if k.startswith(prefix)]
        for k in keys_to_remove:
            _acl_cache.pop(k, None)


# ── Cache warmup (called on server startup) ──

async def warm_acl_cache(agfs) -> int:
    """Scan AGFS for all ``.acl.json`` files and load into cache.

    Called once after server initialization. Returns count of loaded ACLs.
    """
    from openviking.pyagfs.exceptions import AGFSNotFoundError

    count = 0
    # Walk the user/ prefix in AGFS looking for .acl.json files.
    try:
        entries = await agfs.list("viking://user")
    except AGFSNotFoundError:
        logger.info("acl warmup: viking://user not found, cache empty")
        return 0
    except Exception:
        logger.warning("acl warmup: could not list viking://user", exc_info=True)
        return 0

    async def _walk_agfs_dir(path: str) -> None:
        nonlocal count
        try:
            items = await agfs.list(path)
        except (AGFSNotFoundError, Exception):
            return

        for item in items:
            rel = item.get("rel_path") or item.get("name") or ""
            full = f"{path.rstrip('/')}/{rel}"

            if rel == ACL_FILENAME:
                acl = await _read_acl_from_agfs(agfs, full)
                if acl:
                    # Cache at the parent directory URI
                    parent = path.rstrip("/")
                    cache_acl(parent, acl)
                    count += 1
            elif item.get("isDir"):
                await _walk_agfs_dir(full)

    await _walk_agfs_dir("viking://user")
    logger.info("acl warmup complete: %d acl records loaded", count)
    return count
