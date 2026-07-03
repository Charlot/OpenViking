import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Eye, EyeOff, Globe, Lock, Loader2 } from 'lucide-react'

import { Button } from '#/components/ui/button'
import { cn } from '#/lib/utils'

import { normalizeFileUri, normalizeDirUri } from '../../resources/-lib/normalize'

interface AclState {
  shared: boolean
  search_disabled: boolean
  owner?: string
}

interface AclPanelProps {
  uri: string
  isDir: boolean
  className?: string
}

/** Fetch current ACL state for a URI. */
async function fetchAcl(uri: string): Promise<AclState> {
  const normalized = uri.endsWith('/') ? normalizeDirUri(uri) : normalizeFileUri(uri)
  const params = new URLSearchParams({ uri: normalized })
  const resp = await fetch(`/api/v1/acl/get?${params.toString()}`)
  if (!resp.ok) {
    throw new Error(`ACL fetch failed: ${resp.status}`)
  }
  const data = await resp.json()
  return (data.result || { shared: false, search_disabled: false }) as AclState
}

/** Update ACL state for a URI. */
async function updateAcl(
  uri: string,
  shared: boolean,
  searchDisabled: boolean,
): Promise<void> {
  const normalized = uri.endsWith('/') ? normalizeDirUri(uri) : normalizeFileUri(uri)
  const resp = await fetch('/api/v1/acl/set', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      uri: normalized,
      shared,
      search_disabled: searchDisabled,
    }),
  })
  if (!resp.ok) {
    throw new Error(`ACL update failed: ${resp.status}`)
  }
}

export function AclPanel({ uri, isDir, className }: AclPanelProps) {
  const { t } = useTranslation('playground')
  const [acl, setAcl] = useState<AclState | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  const loadAcl = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const result = await fetchAcl(uri)
      setAcl(result)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load ACL')
      setAcl(null)
    } finally {
      setLoading(false)
    }
  }, [uri])

  useEffect(() => {
    void loadAcl()
  }, [loadAcl])

  const toggleShared = useCallback(async () => {
    if (!acl) return
    const next = !acl.shared
    setSaving(true)
    try {
      await updateAcl(uri, next, acl.search_disabled)
      setAcl({ ...acl, shared: next, search_disabled: next ? false : acl.search_disabled })
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to update ACL')
    } finally {
      setSaving(false)
    }
  }, [acl, uri])

  const toggleSearchDisabled = useCallback(async () => {
    if (!acl) return
    const next = !acl.search_disabled
    setSaving(true)
    try {
      await updateAcl(uri, next ? false : acl.shared, next)
      setAcl({ ...acl, search_disabled: next, shared: next ? false : acl.shared })
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to update ACL')
    } finally {
      setSaving(false)
    }
  }, [acl, uri])

  if (loading) {
    return (
      <div className={cn('flex items-center gap-2 px-2 py-3 text-xs text-muted-foreground', className)}>
        <Loader2 className="size-3 animate-spin" />
        <span>Loading ACL…</span>
      </div>
    )
  }

  if (error) {
    return (
      <div className={cn('px-2 py-3 text-xs text-destructive', className)}>
        {error}
        <button
          type="button"
          className="ml-2 underline hover:no-underline"
          onClick={() => void loadAcl()}
        >
          Retry
        </button>
      </div>
    )
  }

  if (!acl) return null

  const stateLabel = acl.search_disabled
    ? 'Disabled (removed from search)'
    : acl.shared
      ? 'Shared (visible to everyone)'
      : 'Private (only you)'

  const StateIcon = acl.search_disabled
    ? EyeOff
    : acl.shared
      ? Globe
      : Lock

  return (
    <div className={cn('space-y-2 px-2 py-3', className)}>
      <div className="flex items-center gap-2 text-xs">
        <StateIcon className="size-3.5 shrink-0" />
        <span className="font-medium text-foreground/80">{stateLabel}</span>
      </div>

      <div className="flex items-center gap-2">
        <Button
          type="button"
          size="sm"
          variant={acl.shared ? 'default' : 'outline'}
          className="h-7 px-2 text-xs"
          disabled={saving || acl.search_disabled}
          onClick={toggleShared}
        >
          <Globe className="mr-1 size-3" />
          {acl.shared ? 'Unshare' : 'Share'}
        </Button>

        <Button
          type="button"
          size="sm"
          variant={acl.search_disabled ? 'destructive' : 'outline'}
          className="h-7 px-2 text-xs"
          disabled={saving}
          onClick={toggleSearchDisabled}
        >
          <EyeOff className="mr-1 size-3" />
          {acl.search_disabled ? 'Restore' : 'Disable'}
        </Button>
      </div>

      {saving && (
        <div className="flex items-center gap-1 text-xs text-muted-foreground">
          <Loader2 className="size-3 animate-spin" />
          Saving…
        </div>
      )}
    </div>
  )
}
