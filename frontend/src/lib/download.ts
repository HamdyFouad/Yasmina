/** Browser download helpers for authenticated binary API responses. */

export function filenameFromDisposition(header: unknown, fallback: string): string {
  if (typeof header !== 'string') {
    return fallback
  }
  const utf8 = /filename\*=UTF-8''([^;]+)/i.exec(header)
  if (utf8?.[1] !== undefined) {
    try {
      return decodeURIComponent(utf8[1].trim())
    } catch {
      return fallback
    }
  }
  const plain = /filename="?([^";]+)"?/i.exec(header)
  if (plain?.[1] !== undefined) {
    return plain[1].trim()
  }
  return fallback
}

export function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  anchor.rel = 'noopener'
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  window.setTimeout(() => {
    URL.revokeObjectURL(url)
  }, 0)
}
