"""Tenant providers connect only to public addresses, with DNS pinned per socket.

TLS SNI and HTTP Host remain the original provider host. Address validation is
performed at socket creation as well as configuration time, preventing DNS
rebinding and redirects from reaching loopback or internal services.
"""
import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit

import httpx
from httpcore._backends.auto import AutoBackend
from httpcore._backends.sync import SyncBackend


def public_addresses(host, port=443):
    try:
        addresses = list(dict.fromkeys(item[4][0] for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)))
    except (OSError, ValueError) as error:
        raise ValueError("PROVIDER_ENDPOINT_UNRESOLVABLE") from error
    if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise ValueError("PROVIDER_ENDPOINT_FORBIDDEN")
    return addresses


def validate_tenant_endpoint(value):
    parsed = urlsplit(value)
    try:
        port = parsed.port or 443
    except ValueError as error:
        raise ValueError("PROVIDER_ENDPOINT_INVALID") from error
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or parsed.query or parsed.fragment or port != 443):
        raise ValueError("PROVIDER_ENDPOINT_INVALID")
    public_addresses(parsed.hostname, port)
    return value.rstrip("/")


class PublicAsyncBackend(AutoBackend):
    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        addresses = await asyncio.to_thread(public_addresses, host, port)
        return await super().connect_tcp(addresses[0], port, timeout, local_address, socket_options)


class PublicSyncBackend(SyncBackend):
    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        return super().connect_tcp(public_addresses(host, port)[0], port, timeout, local_address, socket_options)


def public_async_transport():
    transport = httpx.AsyncHTTPTransport(retries=0)
    transport._pool._network_backend = PublicAsyncBackend()
    return transport


def public_sync_transport():
    transport = httpx.HTTPTransport(retries=0)
    transport._pool._network_backend = PublicSyncBackend()
    return transport
