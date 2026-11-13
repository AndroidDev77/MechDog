"""
ble_probe.py - find MechDog over Bluetooth and map its BLE services (Phase 1).

Step 1: list nearby devices and find the dog
    python ble_probe.py scan

Step 2: dump the dog's services/characteristics and watch anything it sends
    python ble_probe.py probe <address>

On macOS the address is a UUID (e.g. 5A3C...-...), not a MAC address - that's normal.
Close the Hiwonder phone app first; the dog only accepts one BLE connection.

Once we know which characteristic the app writes movement commands to
(capture them with nRF Connect on your phone), dog_link.py will send the same bytes.
"""
import asyncio
import sys

from bleak import BleakClient, BleakScanner


async def scan() -> None:
    print("Scanning for 8 s...")
    devices = await BleakScanner.discover(timeout=8, return_adv=True)
    for addr, (dev, adv) in sorted(devices.items(), key=lambda kv: -kv[1][1].rssi):
        name = dev.name or adv.local_name or "?"
        flag = "   <-- likely the dog" if any(k in name.lower() for k in ("mech", "dog", "hiwonder")) else ""
        print(f"{adv.rssi:>4} dBm  {addr}  {name}{flag}")


async def probe(address: str) -> None:
    def on_notify(char, data: bytearray) -> None:
        print(f"[notify {char.uuid}] {data.hex(' ')}   {bytes(data)!r}")

    async with BleakClient(address) as client:
        print(f"Connected: {client.is_connected}\n")
        for service in client.services:
            print(f"Service {service.uuid}  {service.description}")
            for char in service.characteristics:
                props = ",".join(char.properties)
                print(f"   Char {char.uuid}  [{props}]  {char.description}")
                if "read" in char.properties:
                    try:
                        val = await client.read_gatt_char(char)
                        print(f"      value: {val.hex(' ')}   {bytes(val)!r}")
                    except Exception as e:  # some chars refuse reads
                        print(f"      (read failed: {e})")
                if "notify" in char.properties:
                    await client.start_notify(char, on_notify)
                    print("      (subscribed to notifications)")
        print("\nListening for 30 s - move the dog by hand or tap its sensors...")
        await asyncio.sleep(30)


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "scan":
        asyncio.run(scan())
    elif len(sys.argv) >= 3 and sys.argv[1] == "probe":
        asyncio.run(probe(sys.argv[2]))
    else:
        print(__doc__)
