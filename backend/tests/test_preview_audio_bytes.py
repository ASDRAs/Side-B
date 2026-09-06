import httpx
import pytest

from app.utils.preview_audio import (
    MAX_PREVIEW_BYTES,
    PreviewDownloadError,
    _get_preview_bytes,
)


class Chunks(httpx.AsyncByteStream):
    def __init__(self):
        self.read_count = 0
        self.closed = False

    async def __aiter__(self):
        for chunk in [b"a" * MAX_PREVIEW_BYTES, b"b", b"never read"]:
            self.read_count += 1
            yield chunk

    async def aclose(self):
        self.closed = True


async def test_preview_download_stops_before_buffering_oversized_body():
    chunks = Chunks()
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, stream=chunks))
    ) as http:
        with pytest.raises(PreviewDownloadError):
            await _get_preview_bytes("https://audio.example/preview", http)
    assert chunks.read_count == 2
    assert chunks.closed


async def test_compressed_preview_is_not_decoded_in_backend():
    data = b"compressed fixture"
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, content=data))
    ) as http:
        assert await _get_preview_bytes("https://audio.example/preview", http) == data
