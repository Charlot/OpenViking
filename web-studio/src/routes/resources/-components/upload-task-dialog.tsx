import { ChevronDown, ChevronUp, FileIcon, SearchIcon, XIcon } from 'lucide-react'
import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { Badge } from '#/components/ui/badge'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '#/components/ui/dialog'
import { Input } from '#/components/ui/input'
import { cn } from '#/lib/utils'
import type { ResourceUploadTask } from '../-hooks/use-resource-upload'

function TaskStatusBadge({ status }: { status: ResourceUploadTask['status'] }) {
  const { t } = useTranslation('resources')

  if (status === 'success') {
    return (
      <Badge variant="secondary" className="bg-emerald-500/12 text-emerald-400">
        {t('processingTasks.status.success')}
      </Badge>
    )
  }

  if (status === 'failed') {
    return (
      <Badge variant="secondary" className="bg-rose-500/12 text-black">
        {t('processingTasks.status.failed')}
      </Badge>
    )
  }

  return (
    <Badge variant="secondary" className="bg-amber-500/12 text-amber-300">
      {t('processingTasks.status.processing')}
    </Badge>
  )
}

type UploadTaskDialogProps = {
  open: boolean
  onOpenChange: (open: boolean) => void
  tasks: ResourceUploadTask[]
}

export function UploadTaskDialog({
  open,
  onOpenChange,
  tasks,
}: UploadTaskDialogProps) {
  const { t } = useTranslation('resources')
  const [filter, setFilter] = useState('')
  const [expandedTaskIds, setExpandedTaskIds] = useState<Set<string>>(new Set())

  const sortedTasks = useMemo(
    () => [...tasks].sort((a, b) => b.createdAt - a.createdAt),
    [tasks],
  )

  const filteredTasks = useMemo(() => {
    const q = filter.trim().toLowerCase()
    if (!q) return sortedTasks
    return sortedTasks.filter((task) =>
      (task.serverTaskId || '').toLowerCase().includes(q),
    )
  }, [sortedTasks, filter])

  const toggleTask = (taskId: string) => {
    setExpandedTaskIds((prev) => {
      const next = new Set(prev)
      if (next.has(taskId)) {
        next.delete(taskId)
      } else {
        next.add(taskId)
      }
      return next
    })
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[min(90vh,800px)] gap-0 overflow-hidden p-0 sm:max-w-6xl">
        <DialogHeader className="border-b px-6 py-5 pr-16">
          <DialogTitle className="truncate text-xl">
            {t('processingTasks.title')}
          </DialogTitle>
          <div className="relative mt-3">
            <SearchIcon className="absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
            <Input
              className="h-8 pl-8 pr-8 text-xs"
              placeholder="Filter by ID…"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
            />
            {filter ? (
              <button
                type="button"
                className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                onClick={() => setFilter('')}
              >
                <XIcon className="size-3.5" />
              </button>
            ) : null}
          </div>
        </DialogHeader>

        <div className="max-h-[calc(min(90vh,800px)-5.5rem)] overflow-auto">
          {filteredTasks.length === 0 ? (
            <div className="flex min-h-40 items-center justify-center text-sm text-muted-foreground">
              {filter ? 'No matching tasks' : t('processingTasks.empty')}
            </div>
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border bg-muted/30 text-left text-xs font-medium text-muted-foreground">
                  <th className="w-[320px] px-4 py-3 font-mono">ID</th>
                  <th className="px-4 py-3">{t('processingTasks.columns.fileName')}</th>
                  <th className="px-4 py-3">URI</th>
                  <th className="w-[80px] px-4 py-3">{t('processingTasks.columns.status')}</th>
                  <th className="w-[170px] px-4 py-3">{t('processingTasks.columns.createdAt')}</th>
                </tr>
              </thead>
              <tbody>
                {filteredTasks.map((task) => {
                  const isFailed = task.status === 'failed'

                  return (
                    <tr
                      key={task.id}
                      className={cn(
                        'border-b border-border/50',
                        isFailed && 'bg-rose-500/6',
                      )}
                    >
                      <td className="px-4 py-3 font-mono text-xs text-muted-foreground/80 align-top">
                        {task.serverTaskId || '—'}
                      </td>
                      <td className="px-4 py-3 align-top">
                        <div className="flex items-center gap-2">
                          <FileIcon className="size-4 shrink-0 text-muted-foreground" />
                          <span className="font-medium">{task.fileName}</span>
                        </div>
                      </td>
                      <td className="px-4 py-3 font-mono text-xs text-muted-foreground/70 align-top break-all">
                        {task.rootUri || '—'}
                      </td>
                      <td className="px-4 py-3 align-top">
                        <TaskStatusBadge status={task.status} />
                      </td>
                      <td className="px-4 py-3 text-xs font-mono text-muted-foreground align-top whitespace-nowrap">
                        {new Date(task.createdAt).toLocaleString()}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}
