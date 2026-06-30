# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""ACL permission resolution for OpenViking.

This module provides the ACL hook that extends OpenViking's built-in
permission model with shared search support. It is designed as a
zero-intrusion extension: the core namespace.py makes a single call to
``_resolve_acl_access()``, and all ACL logic lives here and in ``store.py``.

Architecture::

    namespace.is_accessible()
        ├── built-in scope/owner checks
        └── _resolve_acl_access(uri, ctx)  ← ACL hook (this module)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from openviking.core.namespace import UriClassification
    from openviking.server.identity import RequestContext


def resolve_acl_access(
    target: UriClassification,
    ctx: RequestContext,
) -> Optional[bool]:
    """Resolve ACL-based access for a URI.

    Called by ``namespace.is_accessible()`` as the final fallback before
    denying access. Returns:

    * ``True``  — ACL grants access (bypass built-in denial).
    * ``False`` — ACL explicitly denies access.
    * ``None``  — ACL has no opinion; fall through to built-in logic.

    This function is intentionally synchronous and lightweight. ACL data
    is loaded lazily via ``_get_acl()`` from the store module.
    """
    # Only handle user scope; other scopes are handled by built-in logic.
    if target.scope != "user":
        return None

    from openviking.acl.store import get_acl

    acl = get_acl(target.uri)
    if acl is None:
        return None  # No ACL record → built-in logic decides.

    # Owner always has full access, regardless of ACL flags.
    if ctx.user.user_id == acl.get("owner"):
        return True

    # search_disabled only affects search visibility (handled by
    # Search Scope Filter in the vector index layer). For direct
    # read access, shared controls the decision.
    if acl.get("shared"):
        return True

    # Not the owner and not shared → deny.
    return False
