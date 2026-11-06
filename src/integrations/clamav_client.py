"""ClamAV INSTREAM client."""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from pathlib import Path

__all__ = ["ClamAVError", "ClamAVResult", "ClamAVScanner"]

_MAX_CHUNK = 64 * 1024


class ClamAVError(RuntimeError):
    """Raised when clamd is unreachable or returns an unexpected response."""


@dataclass(frozen=True, slots=True)
class ClamAVResult:
    clean: bool
    signature: str | None
    raw: str


class ClamAVScanner:
    """Async clamd INSTREAM scanner.

    The upload path fails closed: any connection/protocol error is surfaced to
    the caller instead of treating the file as clean.
    """

    def __init__(
        self,
        host: str = "localhost",
        port: int = 3310,
        *,
        timeout: float = 30.0,
    ) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout

    async def scan_bytes(self, data: bytes) -> ClamAVResult:
        writer: asyncio.StreamWriter | None = None
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port),
                timeout=self.timeout,
            )
            assert writer is not None
            writer.write(b"zINSTREAM\0")
            for offset in range(0, len(data), _MAX_CHUNK):
                chunk = data[offset : offset + _MAX_CHUNK]
                writer.write(len(chunk).to_bytes(4, "big") + chunk)
            writer.write(b"\0\0\0\0")
            await asyncio.wait_for(writer.drain(), timeout=self.timeout)
            response = await asyncio.wait_for(reader.readuntil(b"\0"), timeout=self.timeout)
        except (
            OSError,
            TimeoutError,
            asyncio.IncompleteReadError,
            asyncio.LimitOverrunError,
        ) as exc:
            raise ClamAVError(f"clamd scan failed: {exc}") from exc
        finally:
            if writer is not None:
                writer.close()
                with contextlib.suppress(OSError, RuntimeError):
                    await writer.wait_closed()

        raw = response.rstrip(b"\0\n").decode("utf-8", "replace")
        if raw.endswith("OK"):
            return ClamAVResult(clean=True, signature=None, raw=raw)
        if "FOUND" in raw:
            signature = raw.split(":", 1)[-1].replace("FOUND", "").strip() or "unknown"
            return ClamAVResult(clean=False, signature=signature, raw=raw)
        raise ClamAVError(f"unexpected clamd response: {raw!r}")

    async def scan_file(self, path: str | Path) -> ClamAVResult:
        return await self.scan_bytes(Path(path).read_bytes())
