import * as React from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle,
  CheckCircle2,
  FileUp,
  Loader2,
  ShieldAlert,
  Upload,
} from 'lucide-react'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { useAuth } from '@/features/auth/auth-context'
import { apiErrorMessage, isApiError } from '@/lib/api-error'
import { unitLabel } from '@/lib/labels'
import { assignTenderOwner, uploadTender } from '@/lib/tenders-api'
import { formatQuantity } from '@/lib/utils'
import { fetchUsers } from '@/lib/users-api'
import type { UploadTenderResponse } from '@/types/domain'

const ACCEPTED_EXTENSIONS = ['.x83', '.d83', '.pdf'] as const

function supportedFile(name: string): boolean {
  const lower = name.toLowerCase()
  return ACCEPTED_EXTENSIONS.some((extension) => lower.endsWith(extension))
}

function formatFileSize(bytes: number): string {
  if (bytes < 1024) {
    return `${bytes} B`
  }
  if (bytes < 1024 * 1024) {
    return `${(bytes / 1024).toFixed(1)} KB`
  }
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function uploadFailure(error: unknown): { title: string; detail: string } {
  if (isApiError(error)) {
    const detail = error.detail
    if (error.status === 413) {
      return { title: 'File is too large', detail }
    }
    if (error.status === 422 && detail.toLowerCase().includes('malware')) {
      return { title: 'Rejected by malware scanning', detail }
    }
    if (error.status === 422) {
      return { title: 'The tender could not be parsed', detail }
    }
    if (error.status === 503) {
      return { title: 'Scanning or storage is unavailable', detail }
    }
    if (error.status === 400) {
      return { title: 'Unsupported file', detail }
    }
  }
  return {
    title: 'Upload failed',
    detail: apiErrorMessage(error),
  }
}

function TenderReview({
  uploaded,
  onOpenTender,
  onUploadAnother,
}: {
  uploaded: UploadTenderResponse
  onOpenTender: () => void
  onUploadAnother: () => void
}) {
  const queryClient = useQueryClient()
  const { hasRole } = useAuth()
  const isAdmin = hasRole('admin')
  const ownerRef = React.useRef<HTMLInputElement>(null)
  const [owner, setOwner] = React.useState(uploaded.tender.owner ?? '')
  const [ownerAttempted, setOwnerAttempted] = React.useState(false)

  const users = useQuery({
    queryKey: ['users'],
    queryFn: fetchUsers,
    enabled: isAdmin,
  })

  const assignOwner = useMutation({
    mutationFn: (username: string) => assignTenderOwner(uploaded.tender_id, username),
    onSuccess: async (data) => {
      setOwner(data.tender.owner ?? '')
      toast.success('Tender owner assigned', {
        description: `${data.tender.tender_number} is now owned by ${data.tender.owner ?? 'nobody'}.`,
      })
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['tenders'] }),
        queryClient.invalidateQueries({ queryKey: ['tender', uploaded.tender_id] }),
        queryClient.invalidateQueries({ queryKey: ['ops', 'summary'] }),
      ])
    },
  })

  const trimmedOwner = owner.trim()
  const ownerError =
    ownerAttempted && trimmedOwner.length === 0 ? 'Enter the username of an active account.' : null

  const handleAssign = (event: React.FormEvent<HTMLFormElement>): void => {
    event.preventDefault()
    setOwnerAttempted(true)
    if (assignOwner.isPending) {
      return
    }
    if (trimmedOwner.length === 0) {
      ownerRef.current?.focus()
      return
    }
    assignOwner.mutate(trimmedOwner)
  }

  return (
    <div className="space-y-5">
      <div
        role="status"
        className="flex items-start gap-3 rounded-md border border-success/30 bg-success/5 p-3"
      >
        <CheckCircle2 className="mt-0.5 h-5 w-5 shrink-0 text-success" aria-hidden="true" />
        <div>
          <p className="text-sm font-medium">Tender parsed and stored</p>
          <p className="text-xs text-muted-foreground">
            {uploaded.tender.tender_number} · {uploaded.tender.title} ·{' '}
            {uploaded.tender.items.length.toLocaleString('de-DE')} position(s)
            {uploaded.warnings.length > 0
              ? ` · ${uploaded.warnings.length.toLocaleString('de-DE')} structural warning(s) to review`
              : ''}
          </p>
        </div>
      </div>

      {uploaded.warnings.length > 0 ? (
        <div className="rounded-md border border-warning/40 bg-warning/5 p-3 text-xs text-warning">
          <div className="flex items-center gap-2 font-medium">
            <AlertTriangle className="h-4 w-4" aria-hidden="true" />
            Structural warnings from the parser
          </div>
          <ul className="mt-2 list-inside list-disc space-y-1">
            {uploaded.warnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        </div>
      ) : (
        <p className="rounded-md border border-border bg-muted/40 p-3 text-xs text-muted-foreground">
          The parser reported no structural warnings.
        </p>
      )}

      <section className="space-y-2" aria-labelledby="upload-positions-heading">
        <div className="flex items-end justify-between gap-2">
          <div>
            <h3 id="upload-positions-heading" className="text-sm font-semibold">
              Parsed positions
            </h3>
            <p className="text-xs text-muted-foreground">
              Review the normalized positions before handing the tender to an owner.
            </p>
          </div>
          <span className="text-xs text-muted-foreground">
            {uploaded.tender.items.length.toLocaleString('de-DE')} item(s)
          </span>
        </div>
        {uploaded.tender.items.length === 0 ? (
          <p className="rounded-md border border-dashed border-border p-3 text-sm text-muted-foreground">
            No positions were extracted. Check the source document or parser warnings.
          </p>
        ) : (
          <div className="max-h-64 overflow-auto rounded-md border border-border">
            <table className="w-full border-collapse text-left text-sm">
              <caption className="sr-only">
                Parsed tender positions with their quantity and unit
              </caption>
              <thead className="sticky top-0 bg-card">
                <tr className="border-b border-border">
                  <th scope="col" className="px-3 py-2 text-xs font-medium uppercase">
                    Pos.
                  </th>
                  <th scope="col" className="px-3 py-2 text-xs font-medium uppercase">
                    Description
                  </th>
                  <th scope="col" className="px-3 py-2 text-right text-xs font-medium uppercase">
                    Quantity
                  </th>
                </tr>
              </thead>
              <tbody>
                {uploaded.tender.items.map((item) => (
                  <tr key={item.id} className="border-b border-border last:border-0">
                    <td className="px-3 py-2 align-top font-mono text-xs">
                      {item.position_number}
                    </td>
                    <td className="px-3 py-2 align-top">
                      <span className="line-clamp-2">{item.short_text}</span>
                    </td>
                    <td className="whitespace-nowrap px-3 py-2 text-right align-top text-xs">
                      {formatQuantity(item.quantity)} {unitLabel(item.unit)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="space-y-2" aria-labelledby="upload-owner-heading">
        <h3 id="upload-owner-heading" className="text-sm font-semibold">
          Owner
        </h3>
        {isAdmin ? (
          <form className="space-y-2" noValidate onSubmit={handleAssign}>
            <Label htmlFor="tender-owner">Owner username</Label>
            <p id="tender-owner-hint" className="text-xs text-muted-foreground">
              Assign the salesperson who will quote this tender. The account must be active.
            </p>
            <div className="flex flex-col gap-2 sm:flex-row">
              <Input
                id="tender-owner"
                ref={ownerRef}
                name="owner"
                value={owner}
                list="tender-owner-options"
                autoComplete="off"
                spellCheck={false}
                aria-invalid={ownerError !== null ? true : undefined}
                aria-describedby={
                  ownerError !== null ? 'tender-owner-hint tender-owner-error' : 'tender-owner-hint'
                }
                onChange={(event) => setOwner(event.target.value)}
                disabled={assignOwner.isPending}
              />
              <datalist id="tender-owner-options">
                {(users.data?.users ?? [])
                  .filter((user) => user.is_active)
                  .map((user) => (
                    <option key={user.username} value={user.username}>
                      {user.username}
                    </option>
                  ))}
              </datalist>
              <Button type="submit" variant="outline" disabled={assignOwner.isPending}>
                {assignOwner.isPending ? <Loader2 className="animate-spin" /> : null}
                Assign owner
              </Button>
            </div>
            {ownerError !== null ? (
              <p id="tender-owner-error" role="alert" className="text-xs text-destructive">
                {ownerError}
              </p>
            ) : null}
            {assignOwner.isError ? (
              <p role="alert" className="text-xs text-destructive">
                {apiErrorMessage(assignOwner.error)}
              </p>
            ) : null}
          </form>
        ) : (
          <p className="rounded-md border border-border bg-muted/40 p-3 text-sm">
            Owned by <span className="font-medium">{uploaded.tender.owner ?? 'unassigned'}</span>. An
            admin can reassign it.
          </p>
        )}
      </section>

      <DialogFooter>
        <Button type="button" variant="outline" onClick={onUploadAnother}>
          Upload another
        </Button>
        <Button type="button" onClick={onOpenTender}>
          Open tender
        </Button>
      </DialogFooter>
    </div>
  )
}

export function UploadTenderDialog({
  onClose,
  onOpenTender,
}: {
  onClose: () => void
  onOpenTender: (tenderId: string) => void
}) {
  const queryClient = useQueryClient()
  const fileRef = React.useRef<HTMLInputElement>(null)
  const reviewHeadingRef = React.useRef<HTMLHeadingElement>(null)
  const [file, setFile] = React.useState<File | null>(null)
  const [fileError, setFileError] = React.useState<string | null>(null)
  const [progress, setProgress] = React.useState(0)
  const [processing, setProcessing] = React.useState(false)
  const [uploaded, setUploaded] = React.useState<UploadTenderResponse | null>(null)

  const upload = useMutation({
    mutationFn: (selected: File) =>
      uploadTender(selected, (percent) => {
        setProgress(percent)
        if (percent >= 100) {
          setProcessing(true)
        }
      }),
    onSuccess: async (data) => {
      setProcessing(false)
      setUploaded(data)
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['tenders'] }),
        queryClient.invalidateQueries({ queryKey: ['ops', 'summary'] }),
      ])
    },
    onError: () => {
      setProgress(0)
      setProcessing(false)
    },
  })

  const handleFileChange = (event: React.ChangeEvent<HTMLInputElement>): void => {
    const selected = event.target.files?.[0] ?? null
    upload.reset()
    setProgress(0)
    setProcessing(false)
    if (selected === null) {
      setFile(null)
      setFileError(null)
      return
    }
    if (!supportedFile(selected.name)) {
      setFile(null)
      setFileError('Choose a .x83, .d83 or .pdf file.')
      return
    }
    setFile(selected)
    setFileError(null)
  }

  const handleSubmit = (event: React.FormEvent<HTMLFormElement>): void => {
    event.preventDefault()
    if (upload.isPending) {
      return
    }
    if (file === null) {
      setFileError('Choose a tender file before uploading.')
      fileRef.current?.focus()
      return
    }
    if (!supportedFile(file.name)) {
      setFileError('Choose a .x83, .d83 or .pdf file.')
      fileRef.current?.focus()
      return
    }
    setFileError(null)
    upload.mutate(file)
  }

  const reset = (): void => {
    upload.reset()
    setFile(null)
    setFileError(null)
    setProgress(0)
    setProcessing(false)
    setUploaded(null)
    if (fileRef.current !== null) {
      fileRef.current.value = ''
    }
  }

  const failure = upload.error !== null ? uploadFailure(upload.error) : null

  React.useEffect(() => {
    if (uploaded !== null) {
      reviewHeadingRef.current?.focus()
    }
  }, [uploaded])

  return (
    <Dialog open onOpenChange={(open) => (!open ? onClose() : undefined)}>
      <DialogContent className="max-h-[92vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle ref={reviewHeadingRef} tabIndex={-1}>
            {uploaded !== null ? 'Review uploaded tender' : 'Upload tender'}
          </DialogTitle>
          <DialogDescription>
            {uploaded !== null
              ? 'Check the normalized positions and assign an owner before creating an offer.'
              : 'Upload a GAEB .x83/.d83 or PDF tender. The file is scanned by ClamAV before parsing and stored in object storage.'}
          </DialogDescription>
        </DialogHeader>

        {uploaded !== null ? (
          <TenderReview
            uploaded={uploaded}
            onUploadAnother={reset}
            onOpenTender={() => onOpenTender(uploaded.tender_id)}
          />
        ) : (
          <form className="space-y-4" noValidate onSubmit={handleSubmit}>
            <div className="space-y-1.5">
              <Label htmlFor="tender-file">
                Tender file
                <span aria-hidden="true"> *</span>
              </Label>
              <p id="tender-file-hint" className="text-xs text-muted-foreground">
                Accepted formats: GAEB .x83, .d83 and PDF. Maximum size is enforced by the API.
              </p>
              <Input
                id="tender-file"
                ref={fileRef}
                name="file"
                type="file"
                accept={ACCEPTED_EXTENSIONS.join(',')}
                aria-invalid={fileError !== null ? true : undefined}
                aria-describedby={
                  fileError !== null ? 'tender-file-hint tender-file-error' : 'tender-file-hint'
                }
                onChange={handleFileChange}
                disabled={upload.isPending}
              />
              {file !== null ? (
                <p className="flex items-center gap-2 text-xs text-muted-foreground">
                  <FileUp className="h-3.5 w-3.5" aria-hidden="true" />
                  <span className="truncate">{file.name}</span>
                  <span>({formatFileSize(file.size)})</span>
                </p>
              ) : null}
              {fileError !== null ? (
                <p id="tender-file-error" role="alert" className="text-xs text-destructive">
                  {fileError}
                </p>
              ) : null}
            </div>

            {upload.isPending ? (
              <div className="space-y-2 rounded-md border border-border bg-muted/40 p-3">
                <p className="text-sm font-medium">
                  {processing
                    ? 'Scanning and parsing the tender…'
                    : `Uploading ${progress.toLocaleString('de-DE')}%`}
                </p>
                <progress
                  aria-label="Tender upload progress"
                  className="h-2 w-full"
                  max={100}
                  {...(processing ? {} : { value: progress })}
                />
                <p className="text-xs text-muted-foreground">
                  ClamAV runs first. A malicious file is rejected before any parser sees it.
                </p>
              </div>
            ) : null}

            {failure !== null ? (
              <div
                role="alert"
                className="flex items-start gap-3 rounded-md border border-destructive/40 bg-destructive/5 p-3"
              >
                {failure.title === 'Rejected by malware scanning' ? (
                  <ShieldAlert className="mt-0.5 h-5 w-5 shrink-0 text-destructive" aria-hidden="true" />
                ) : (
                  <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-destructive" aria-hidden="true" />
                )}
                <div>
                  <p className="text-sm font-medium text-destructive">{failure.title}</p>
                  <p className="text-xs text-muted-foreground">{failure.detail}</p>
                </div>
              </div>
            ) : null}

            <DialogFooter>
              <Button type="button" variant="outline" onClick={onClose} disabled={upload.isPending}>
                Cancel
              </Button>
              <Button type="submit" disabled={upload.isPending}>
                {upload.isPending ? <Loader2 className="animate-spin" /> : <Upload />}
                Upload tender
              </Button>
            </DialogFooter>
          </form>
        )}
      </DialogContent>
    </Dialog>
  )
}
