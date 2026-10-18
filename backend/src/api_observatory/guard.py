"""Single-process public request admission and body limits (before JSON parsing)."""

import asyncio
import ipaddress
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

from starlette.responses import JSONResponse


@dataclass
class Quota:
    minute_start: float
    day_start: float
    minute: int = 0
    day: int = 0


class PublicGuard:
    def __init__(self, app, *, settings):
        self.app = app
        self.settings = settings
        self.clients: dict[str, Quota] = {}
        self.global_quota = Quota(time.monotonic(), time.monotonic())
        self.active = 0

    def admit(self, ip: str) -> bool:
        now = time.monotonic()
        if ip not in self.clients:
            if len(self.clients) >= 10000:
                self.clients = {
                    key: quota
                    for key, quota in self.clients.items()
                    if now - quota.day_start < 86400
                }
            if len(self.clients) >= 10000:
                return False
            self.clients[ip] = Quota(now, now)
        quota = self.clients[ip]
        for item in (quota, self.global_quota):
            if now - item.minute_start >= 60:
                item.minute_start, item.minute = now, 0
            if now - item.day_start >= 86400:
                item.day_start, item.day = now, 0
        if (
            quota.minute >= self.settings.public_requests_per_minute
            or quota.day >= self.settings.public_requests_per_day
            or self.global_quota.day >= self.settings.public_global_requests_per_day
        ):
            return False
        quota.minute += 1
        quota.day += 1
        self.global_quota.day += 1
        return True

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def secure_send(message):
            if message["type"] == "http.response.start":
                message.setdefault("headers", []).extend(
                    [
                        (b"cache-control", b"no-store"),
                        (b"x-content-type-options", b"nosniff"),
                        (b"referrer-policy", b"no-referrer"),
                        (b"x-frame-options", b"DENY"),
                        (
                            b"content-security-policy",
                            b"default-src 'self'; script-src 'self'; "
                            b"style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
                            b"connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; "
                            b"form-action 'self'",
                        ),
                    ]
                )
            await send(message)

        async def reject(code, detail, headers=None):
            await JSONResponse({"detail": detail}, status_code=code, headers=headers)(
                scope, receive, secure_send
            )

        headers = dict(scope["headers"])
        local_path = (
            scope["path"]
            in {
                "/api/test-config",
                "/api/providers",
                "/api/provider-tests",
            }
            or scope["path"].startswith("/api/runs")
            or scope["path"] in {"/api/comparisons", "/api/insights"}
            or scope["path"].startswith("/api/benchmark")
        )
        if self.settings.public_mode and local_path and scope["path"] != "/api/test-config":
            return await reject(404, "Not found.")
        if not self.settings.public_mode and local_path:
            peer = (scope.get("client") or ("",))[0]
            try:
                loopback = ipaddress.ip_address(peer).is_loopback
                host = urlsplit("http://" + headers.get(b"host", b"").decode()).hostname
            except ValueError:
                loopback, host = False, None
            if not loopback or host not in {"localhost", "127.0.0.1", "::1"}:
                return await reject(403, "本地 Provider 仅允许本机访问。")
        if scope["path"].startswith("/api/"):
            origin = headers.get(b"origin")
            expected = scope["scheme"].encode() + b"://" + headers.get(b"host", b"")
            if origin and origin != expected:
                return await reject(403, "不允许跨站调用测试接口。")

        if scope["path"].rstrip("/") not in {
            "/api/self-tests",
            "/api/self-runs",
            "/api/self-comparisons",
        }:
            return await self.app(scope, receive, secure_send)
        if scope["method"] != "POST":
            return await self.app(scope, receive, secure_send)
        if self.active >= self.settings.public_max_concurrency:
            return await reject(429, "测试服务繁忙，请稍后重试。", {"Retry-After": "20"})
        ip = (scope.get("client") or ("unknown",))[0]
        if not self.admit(ip):
            return await reject(429, "已达到测试次数限制，请稍后重试。", {"Retry-After": "60"})
        if headers.get(b"content-type", b"").split(b";")[0].strip() != b"application/json":
            return await reject(415, "请使用 JSON 提交测试参数。")
        self.active += 1
        started = False

        async def track_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await secure_send(message)

        try:
            body = bytearray()
            async with asyncio.timeout(10):
                while True:
                    message = await receive()
                    if message["type"] == "http.disconnect":
                        return
                    body.extend(message.get("body", b""))
                    if len(body) > 16384:
                        return await reject(413, "请求内容过大。")
                    if not message.get("more_body", False):
                        break
            delivered = False

            async def replay():
                nonlocal delivered
                if delivered:
                    return await receive()
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}

            async with asyncio.timeout(self.settings.public_run_timeout_seconds):
                await self.app(scope, replay, track_send)
        except TimeoutError:
            if not started:
                await reject(504, "测试超过时间限制，请减少样本数后重试。")
        finally:
            self.active -= 1
