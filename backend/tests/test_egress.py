import asyncio
import socket

import httpcore
import httpx
import pytest

from api_observatory.egress import BoundedStream, PublicHTTPTransport, PublicNetworkBackend


@pytest.mark.parametrize(
    "addresses", [["127.0.0.1"], ["::1"], ["10.2.3.4"], ["93.184.216.34", "192.168.1.1"], []]
)
def test_private_or_mixed_dns_never_reaches_socket(monkeypatch, addresses):
    async def run():
        async def resolve(*args, **kwargs):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443)) for ip in addresses]

        async def forbidden(*args, **kwargs):
            pytest.fail("Private address reached TCP connect")

        monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", resolve)
        monkeypatch.setattr(httpcore.AnyIOBackend, "connect_tcp", forbidden)
        with pytest.raises(httpcore.ConnectError):
            await PublicNetworkBackend().connect_tcp("attacker.example", 443)

    asyncio.run(run())


def test_validated_ip_is_pinned_but_tls_and_host_keep_original_hostname(monkeypatch):
    async def run():
        connections, tls_names, writes = [], [], []
        resolutions = 0

        async def resolve(*args, **kwargs):
            nonlocal resolutions
            resolutions += 1
            # A second hostname lookup would return a private IP (DNS rebinding).
            ip = "93.184.216.34" if resolutions == 1 else "127.0.0.1"
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443))]

        class Stream(httpcore.AsyncMockStream):
            async def start_tls(self, ssl_context, server_hostname=None, timeout=None):
                assert ssl_context.check_hostname
                tls_names.append(server_hostname)
                return self

            async def write(self, buffer, timeout=None):
                writes.append(buffer)

        async def connect(self, host, port, *args):
            connections.append((host, port))
            return Stream([b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nOK"])

        monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", resolve)
        monkeypatch.setattr(httpcore.AnyIOBackend, "connect_tcp", connect)
        async with httpx.AsyncClient(transport=PublicHTTPTransport(), trust_env=False) as client:
            response = await client.get("https://api.example.com/v1")
            assert response.text == "OK"
        assert resolutions == 1
        assert connections == [("93.184.216.34", 443)]
        assert tls_names == ["api.example.com"]
        assert b"Host: api.example.com" in b"".join(writes)

    asyncio.run(run())


def test_stream_has_byte_and_wall_clock_limits():
    async def run():
        async def large():
            yield b"0123456789"
            yield b"0123456789"

        stream = BoundedStream(large(), asyncio.get_running_loop().time() + 1, 15)
        with pytest.raises(httpx.ReadError):
            async for _ in stream:
                pass
        await stream.aclose()

        async def endless():
            while True:
                await asyncio.sleep(0.005)
                yield b": keepalive\n\n"

        stream = BoundedStream(endless(), asyncio.get_running_loop().time() + 0.02, 10000)
        with pytest.raises(httpx.ReadTimeout):
            async for _ in stream:
                pass
        await stream.aclose()

    asyncio.run(run())


def test_compressed_responses_are_closed_without_decompression(monkeypatch):
    class Pool:
        closed = False

        async def handle_async_request(self, request):
            assert (b"accept-encoding", b"identity") in request.headers

            async def body():
                pytest.fail("Compressed body must not be consumed")
                yield b""

            response = httpcore.Response(
                200, headers=[(b"content-encoding", b"gzip")], content=body()
            )

            async def close():
                self.closed = True

            response.aclose = close
            return response

    async def run():
        transport = PublicHTTPTransport()
        pool = Pool()
        monkeypatch.setattr(transport, "pool", pool)
        with pytest.raises(httpx.ReadError):
            await transport.handle_async_request(httpx.Request("GET", "https://example.com"))
        assert pool.closed

    asyncio.run(run())
