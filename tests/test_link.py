import socket
import threading
import time

from dogbrain.link import DogLink, SimTransport, _BleTransport


def test_tcp_link_against_fake_dog():
    received = []

    def fake_dog(srv):
        conn, _ = srv.accept()
        buf = ""
        while True:
            data = conn.recv(256)
            if not data:
                return
            buf += data.decode()
            while "$" in buf:
                msg, buf = buf.split("$", 1)
                f = msg[4:].strip("|").split("|")
                received.append(f)
                if f[0] == "15":
                    conn.sendall(b"CMD|15|1234|1.5|-2.0|7900|walking|$")
                if f[0] == "11" and int(f[1]) > 70:
                    conn.sendall(b"junkCMD|19|blocked|120|$")

    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    threading.Thread(target=fake_dog, args=(srv,), daemon=True).start()

    link = DogLink.tcp("127.0.0.1", srv.getsockname()[1])
    events = []
    link.on_event = lambda e, d: events.append((e, d))
    link.walk(50, 10)
    link.action("sit_dowm")
    link.walk(80, 0)
    time.sleep(1.0)
    link.close()

    assert ["11", "50", "10"] in received
    assert ["12", "sit_dowm"] in received
    assert [f[0] for f in received].count("15") >= 2          # heartbeat
    assert link.status["distance_mm"] == 1234
    assert ("blocked", "120") in events and link.walking is False


def test_sim_transport_and_wait_event():
    link = DogLink.sim()
    t = time.time()
    link.action("handshake")
    assert link.wait_event("done", t, 2) == ("done", "handshake")
    link.t.distance_mm = 100
    link.walk(60, 0)
    assert link.wait_event("blocked", t, 2) is not None
    link.close()


def test_ble_writes_are_chunked():
    import asyncio
    t = _BleTransport.__new__(_BleTransport)
    writes = []

    class FakeClient:
        async def write_gatt_char(self, ch, data, response):
            assert len(data) <= 20
            writes.append(data)

    t.client, t.write_char, t.with_response, t.chunk = FakeClient(), "c", False, 20
    t.loop = asyncio.new_event_loop()
    threading.Thread(target=t.loop.run_forever, daemon=True).start()
    t.write(b"CMD|12|stretch_oneself|$")
    assert b"".join(writes) == b"CMD|12|stretch_oneself|$" and len(writes) == 2
