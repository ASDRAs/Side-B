import httpx
import pytest

from preview import PreviewTooLargeError, _limited_stream


def client_for(body: bytes, headers=None):
    def handler(request):
        return httpx.Response(200, content=body, headers=headers or {})

    return lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def collect(stream):
    return b"".join([chunk async for chunk in stream])


async def test_relay_passes_bytes_up_to_the_limit():
    body = b"a" * 20_000

    assert (
        await collect(
            _limited_stream("https://cdn.example/a", 20_000, client_for(body))
        )
        == body
    )


async def test_relay_aborts_when_the_body_exceeds_the_limit():
    with pytest.raises(PreviewTooLargeError):
        await collect(
            _limited_stream("https://cdn.example/a", 10_000, client_for(b"a" * 20_000))
        )


async def test_relay_refuses_a_declared_oversized_body_before_sending_bytes():
    stream = _limited_stream(
        "https://cdn.example/a",
        10,
        client_for(b"a" * 5, headers={"Content-Length": "999999"}),
    )

    with pytest.raises(PreviewTooLargeError):
        await stream.__anext__()
