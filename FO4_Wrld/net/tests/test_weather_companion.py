"""B6.11 weather + B6.8 companion wire/server tests (protocol v21)."""
from __future__ import annotations

import asyncio
import socket
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from protocol import (  # noqa: E402
    MessageType,
    HelloPayload, AckPayload,
    WeatherSetPayload, WeatherBroadcastPayload, WeatherStateBootPayload,
    CompanionSetPayload, CompanionBroadcastPayload,
    CompanionStateBootPayload, CompanionStateEntry,
    encode_frame, decode_frame,
)
from server.state import ServerState  # noqa: E402
from server.main import ServerProtocol  # noqa: E402


class TestWeatherWire:
    def test_set_roundtrip(self):
        p = WeatherSetPayload(weather_form_id=0x1F0, timestamp_ms=1)
        assert WeatherSetPayload.decode(p.encode()) == p

    def test_bcast_roundtrip(self):
        p = WeatherBroadcastPayload(
            peer_id="a", weather_form_id=0xABC, timestamp_ms=2)
        p2 = WeatherBroadcastPayload.decode(p.encode())
        assert p2.peer_id == "a" and p2.weather_form_id == 0xABC

    def test_boot_roundtrip(self):
        p = WeatherStateBootPayload(weather_form_id=0x123, timestamp_ms=9)
        assert WeatherStateBootPayload.decode(p.encode()) == p


class TestCompanionWire:
    def test_set_roundtrip(self):
        p = CompanionSetPayload(
            actor_form_id=0x1D15C, teammate=1, can_do_favor=1, timestamp_ms=3)
        assert CompanionSetPayload.decode(p.encode()) == p

    def test_bcast_roundtrip(self):
        p = CompanionBroadcastPayload(
            peer_id="b", actor_form_id=0x1D15C,
            teammate=0, can_do_favor=1, timestamp_ms=4)
        p2 = CompanionBroadcastPayload.decode(p.encode())
        assert p2.peer_id == "b" and p2.teammate == 0

    def test_boot_roundtrip(self):
        entries = (
            CompanionStateEntry(0x111, 1, 1),
            CompanionStateEntry(0x222, 0, 0),
        )
        p = CompanionStateBootPayload(entries=entries)
        assert CompanionStateBootPayload.decode(p.encode()) == p


async def _start(state: ServerState, port: int):
    loop = asyncio.get_running_loop()
    transport, protocol = await loop.create_datagram_endpoint(
        lambda: ServerProtocol(state), local_addr=("127.0.0.1", port))
    from net.tests.test_server_integration import _periodic_tick_driver
    loop.create_task(_periodic_tick_driver(protocol, 20))
    return transport, protocol


async def _hello(sock, addr, cid: str):
    hello = HelloPayload(
        client_id=cid, client_version_major=1, client_version_minor=0)
    sock.sendto(encode_frame(MessageType.HELLO, 0, hello, reliable=True), addr)
    sock.setblocking(False)
    deadline = asyncio.get_running_loop().time() + 2.0
    while asyncio.get_running_loop().time() < deadline:
        try:
            data, _ = sock.recvfrom(4096)
        except BlockingIOError:
            await asyncio.sleep(0.02)
            continue
        frame = decode_frame(data)
        if frame.header.msg_type == int(MessageType.WELCOME):
            ack = AckPayload(
                highest_contiguous_seq=frame.header.seq, sack_bitmap=0)
            sock.sendto(
                encode_frame(MessageType.ACK, 0, ack, reliable=False), addr)
            return
    raise AssertionError("WELCOME timeout")


async def _collect(sock, duration_s: float):
    out = []
    loop = asyncio.get_running_loop()
    deadline = loop.time() + duration_s
    sock.setblocking(False)
    while loop.time() < deadline:
        try:
            data, addr = sock.recvfrom(4096)
        except BlockingIOError:
            await asyncio.sleep(0.01)
            continue
        try:
            frame = decode_frame(data)
        except Exception:
            continue
        out.append((frame, addr))
        if frame.header.flags & 0x01:
            ack = AckPayload(
                highest_contiguous_seq=frame.header.seq, sack_bitmap=0)
            sock.sendto(
                encode_frame(MessageType.ACK, 0, ack, reliable=False), addr)
    return out


@pytest.mark.asyncio
async def test_weather_set_broadcasts_and_bootstraps():
    port = 31550
    state = ServerState()
    transport, _ = await _start(state, port)
    try:
        server = ("127.0.0.1", port)
        a = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        b = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        a.bind(("127.0.0.1", 0))
        b.bind(("127.0.0.1", 0))
        try:
            await asyncio.gather(_hello(a, server, "wA"), _hello(b, server, "wB"))
            await _collect(b, 0.3)
            await _collect(a, 0.0)

            op = WeatherSetPayload(weather_form_id=0x2B01, timestamp_ms=1)
            a.sendto(
                encode_frame(MessageType.WEATHER_SET, 1, op, reliable=True),
                server)
            frames_b = await _collect(b, 0.8)
            bcasts = [
                f.payload for f, _ in frames_b
                if f.header.msg_type == int(MessageType.WEATHER_BCAST)]
            assert len(bcasts) == 1
            assert bcasts[0].weather_form_id == 0x2B01
            assert state.weather_form_id == 0x2B01

            # late join gets bootstrap
            c = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            c.bind(("127.0.0.1", 0))
            try:
                await _hello(c, server, "wC")
                frames_c = await _collect(c, 0.8)
                boots = [
                    f.payload for f, _ in frames_c
                    if f.header.msg_type == int(MessageType.WEATHER_STATE_BOOT)]
                assert boots and boots[0].weather_form_id == 0x2B01
            finally:
                c.close()
        finally:
            a.close()
            b.close()
    finally:
        transport.close()


@pytest.mark.asyncio
async def test_companion_set_broadcasts():
    port = 31551
    state = ServerState()
    transport, _ = await _start(state, port)
    try:
        server = ("127.0.0.1", port)
        a = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        b = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        a.bind(("127.0.0.1", 0))
        b.bind(("127.0.0.1", 0))
        try:
            await asyncio.gather(_hello(a, server, "cA"), _hello(b, server, "cB"))
            await _collect(b, 0.3)
            await _collect(a, 0.0)

            op = CompanionSetPayload(
                actor_form_id=0x1D15C, teammate=1, can_do_favor=1,
                timestamp_ms=5)
            a.sendto(
                encode_frame(MessageType.COMPANION_SET, 1, op, reliable=True),
                server)
            frames_b = await _collect(b, 0.8)
            bcasts = [
                f.payload for f, _ in frames_b
                if f.header.msg_type == int(MessageType.COMPANION_BCAST)]
            assert len(bcasts) == 1
            assert bcasts[0].actor_form_id == 0x1D15C
            assert bcasts[0].teammate == 1
            comps = state.all_companions()
            assert len(comps) == 1 and comps[0].teammate is True
        finally:
            a.close()
            b.close()
    finally:
        transport.close()
