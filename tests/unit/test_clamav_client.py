"""ClamAV INSTREAM client tests."""

from __future__ import annotations

import asyncio

import pytest

from src.integrations.clamav_client import ClamAVError, ClamAVScanner


async def _start_clamd(response: bytes):
    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.readuntil(b"\0")
        while True:
            size = int.from_bytes(await reader.readexactly(4), "big")
            if size == 0:
                break
            await reader.readexactly(size)
        writer.write(response)
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    return server, port


def test_clean_stream() -> None:
    async def scenario() -> None:
        server, port = await _start_clamd(b"stream: OK\0")
        try:
            result = await ClamAVScanner("127.0.0.1", port, timeout=2).scan_bytes(b"hello")
        finally:
            server.close()
            await server.wait_closed()
        assert result.clean is True
        assert result.signature is None

    asyncio.run(scenario())


def test_malware_detection() -> None:
    async def scenario() -> None:
        server, port = await _start_clamd(b"stream: Eicar-Test-Signature FOUND\0")
        try:
            result = await ClamAVScanner("127.0.0.1", port, timeout=2).scan_bytes(b"hello")
        finally:
            server.close()
            await server.wait_closed()
        assert result.clean is False
        assert result.signature == "Eicar-Test-Signature"

    asyncio.run(scenario())


def test_unexpected_response_raises() -> None:
    async def scenario() -> None:
        server, port = await _start_clamd(b"stream: ERROR\0")
        try:
            with pytest.raises(ClamAVError, match="unexpected"):
                await ClamAVScanner("127.0.0.1", port, timeout=2).scan_bytes(b"hello")
        finally:
            server.close()
            await server.wait_closed()

    asyncio.run(scenario())
