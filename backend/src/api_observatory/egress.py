"""Public HTTPS egress with DNS checks at the actual TCP connection boundary."""

import asyncio
import ipaddress
import socket
import ssl
from collections.abc import AsyncIterator
from contextlib import contextmanager

import httpcore
import httpx
from pydantic import AnyHttpUrl


def public_address(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value.strip("[]"))
    except ValueError:
        return False
    # Transition mechanisms can embed private IPv4 destinations in global IPv6.
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped or address.sixtofour or address.teredo:
            return False
        if address in ipaddress.ip_network("64:ff9b::/96") or address in ipaddress.ip_network(
            "64:ff9b:1::/48"
        ):
            return False
    return address.is_global and not address.is_multicast and not address.is_reserved


def validate_public_endpoint(endpoint: AnyHttpUrl) -> AnyHttpUrl:
    if endpoint.scheme != "https" or endpoint.port != 443:
        raise ValueError("endpoint must use HTTPS on port 443")
    if endpoint.username or endpoint.password or endpoint.query or endpoint.fragment:
        raise ValueError("endpoint must not include credentials, query or fragment")
    host = (endpoint.host or "").lower().rstrip(".").strip("[]")
    if (
        "%" in host
        or host == "localhost"
        or host.endswith((".localhost", ".local", ".internal", ".home.arpa"))
    ):
        raise ValueError("endpoint must be public")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        if "." not in host:
            raise ValueError("endpoint must be public") from None
    else:
        if not public_address(host):
            raise ValueError("endpoint must be public")
    return endpoint


class PublicNetworkBackend(httpcore.AsyncNetworkBackend):
    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        try:
            async with asyncio.timeout(timeout or 10):
                addresses = await asyncio.get_running_loop().getaddrinfo(
                    host, port, type=socket.SOCK_STREAM
                )
                ips = list(dict.fromkeys(item[4][0] for item in addresses))
                if port != 443 or not ips or not all(public_address(ip) for ip in ips):
                    raise httpcore.ConnectError("Endpoint is not a public HTTPS destination.")
                # Only numeric, already validated IPs reach the socket backend. TLS still
                # receives the original hostname from httpcore for SNI and verification.
                backend = httpcore.AnyIOBackend()
                for ip in ips:
                    try:
                        return await backend.connect_tcp(
                            ip, port, timeout, local_address, socket_options
                        )
                    except httpcore.ConnectError:
                        continue
                raise httpcore.ConnectError("Endpoint connection failed.")
        except TimeoutError:
            raise httpcore.ConnectTimeout("Endpoint lookup or connection timed out.") from None
        except OSError:
            raise httpcore.ConnectError("Endpoint lookup failed.") from None


@contextmanager
def transport_errors():
    try:
        yield
    except httpcore.TimeoutException:
        raise httpx.ReadTimeout("Provider request timed out.") from None
    except (httpcore.NetworkError, httpcore.ProtocolError):
        raise httpx.ConnectError("Provider connection failed.") from None


class BoundedStream(httpx.AsyncByteStream):
    def __init__(self, stream, deadline: float, max_bytes: int):
        self.stream = stream
        self.deadline = deadline
        self.max_bytes = max_bytes

    async def __aiter__(self) -> AsyncIterator[bytes]:
        received = 0
        try:
            with transport_errors():
                async with asyncio.timeout_at(self.deadline):
                    async for chunk in self.stream:
                        received += len(chunk)
                        if received > self.max_bytes:
                            raise httpx.ReadError("Provider response exceeded the size limit.")
                        yield chunk
        except TimeoutError:
            raise httpx.ReadTimeout("Provider exceeded its total time limit.") from None

    async def aclose(self):
        await self.stream.aclose()


class PublicHTTPTransport(httpx.AsyncBaseTransport):
    def __init__(self, *, seconds: float = 20, max_bytes: int = 1_048_576):
        self.seconds = seconds
        self.max_bytes = max_bytes
        self.pool = httpcore.AsyncConnectionPool(
            ssl_context=ssl.create_default_context(),
            network_backend=PublicNetworkBackend(),
            max_connections=4,
            max_keepalive_connections=0,
            retries=0,
        )

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        try:
            validate_public_endpoint(AnyHttpUrl(str(request.url)))
        except ValueError:
            raise httpx.ConnectError("Endpoint is not permitted.") from None
        deadline = asyncio.get_running_loop().time() + self.seconds
        request.headers["accept-encoding"] = "identity"
        try:
            with transport_errors():
                async with asyncio.timeout_at(deadline):
                    response = await self.pool.handle_async_request(
                        httpcore.Request(
                            method=request.method,
                            url=httpcore.URL(
                                scheme=request.url.raw_scheme,
                                host=request.url.raw_host,
                                port=request.url.port,
                                target=request.url.raw_path,
                            ),
                            headers=request.headers.raw,
                            content=request.stream,
                            extensions=request.extensions,
                        )
                    )
        except TimeoutError:
            raise httpx.ReadTimeout("Provider exceeded its total time limit.") from None
        headers = httpx.Headers(response.headers)
        # Refuse compression rather than decompress unbounded attacker-controlled data.
        if headers.get("content-encoding", "identity").lower() != "identity":
            await response.aclose()
            raise httpx.ReadError("Compressed provider responses are not supported.")
        return httpx.Response(
            response.status,
            headers=headers,
            stream=BoundedStream(response.stream, deadline, self.max_bytes),
            extensions=response.extensions,
        )

    async def aclose(self):
        await self.pool.aclose()
