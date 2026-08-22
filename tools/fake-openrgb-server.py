#!/usr/bin/env python3
"""A stub OpenRGB SDK server, just enough for a real end-to-end run.

Written to verify the three-zone work without hardware, and kept because that
is a permanent problem rather than a one-off: this project is developed against
one specific board and card, and nobody else has them. With this running, the
whole stack can be exercised on any machine —

    tools/fake-openrgb-server.py 6788 &
    mimarchy-setup --list --port 6788
    mimarchy-setup --port 6788          # writes a config against these devices
    mimarchy-lightd --once              # logs the resize and frame writes

— which is what makes "does a third strip actually work" answerable by running
it rather than by reading the code. It logs what it receives, so a wrong zone
length or a frame going to the wrong device shows up directly.

It is a stub, not an implementation: it speaks the handshake and the two
enumeration packets and accepts writes, and nothing else. Anything beyond
enumeration and frame-writing will need adding to it.

Serves a board with an
addressable header plus an Aura Core zone, a one-LED GPU, and a third strip.
"""
import socket
import struct
import sys
import threading

from openrgb import utils as u

VERSION = 3


def mode(mode_id, name, flags=u.ModeFlags(0), speed_min=None, speed_max=None,
         speed=None):
    return u.ModeData(
        id=mode_id, name=name, value=mode_id, flags=flags,
        speed_min=speed_min, speed_max=speed_max,
        brightness_min=None, brightness_max=None,
        colors_min=None, colors_max=None,
        speed=speed, brightness=None, direction=None, color_mode=0,
        colors=None,
    )


def zone(name, zone_type, num_leds, leds_min, leds_max):
    return u.ZoneData(
        name=name, zone_type=zone_type, leds_min=leds_min, leds_max=leds_max,
        num_leds=num_leds, mat_height=0, mat_width=0, matrix_map=None,
        segments=[], leds=[u.LEDData(name=f"{name} {i}", value=0)
                           for i in range(num_leds)],
        colors=[u.RGBColor(0, 0, 0) for _ in range(num_leds)], start_idx=0,
    )


def controller(name, device_type, zones, modes):
    leds = [led for z in zones for led in z.leds]
    return u.ControllerData(
        name=name,
        metadata=u.MetaData(vendor="Fake", description="fake device",
                            version="1.0", serial="0", location="fake"),
        device_type=device_type, leds=leds, zones=zones, modes=modes,
        colors=[u.RGBColor(0, 0, 0) for _ in leds], active_mode=0,
    )


BOARD_MODES = [mode(0, "Direct"), mode(1, "Off"), mode(2, "Static"),
               mode(3, "Breathing"), mode(4, "Spectrum Cycle"),
               mode(5, "Rainbow"), mode(6, "Chase")]
GPU_MODES = [mode(0, "Static"), mode(1, "Off"),
             mode(2, "Rainbow Wave", flags=u.ModeFlags.HAS_SPEED, speed_min=250, speed_max=10,
                  speed=50),
             mode(3, "Runway", flags=u.ModeFlags.HAS_SPEED, speed_min=50, speed_max=5,
                  speed=20),
             mode(4, "Spectrum Cycle")]

DEVICES = [
    controller("ASUS PRIME X870-P WIFI", u.DeviceType.MOTHERBOARD,
               [zone("Addressable 1", u.ZoneType.LINEAR, 0, 0, 120),
                zone("Aura Core", u.ZoneType.SINGLE, 1, 1, 1)],
               BOARD_MODES),
    controller("Sapphire Radeon RX 9070 XT Nitro+", u.DeviceType.GPU,
               [zone("GPU Zone", u.ZoneType.SINGLE, 1, 1, 1)], GPU_MODES),
    controller("Corsair Lighting Node Pro", u.DeviceType.LEDSTRIP,
               [zone("Channel 1", u.ZoneType.LINEAR, 0, 0, 204)], BOARD_MODES),
]


def header(device_id, packet_type, size):
    return struct.pack("ccccIII", b"O", b"R", b"G", b"B",
                       device_id, packet_type, size)


def serve(conn):
    while True:
        raw = conn.recv(16)
        if len(raw) < 16:
            return
        buff = list(struct.unpack("ccccIII", raw))
        device_id, packet_type, size = buff[4:]
        body = b""
        while len(body) < size:
            body += conn.recv(size - len(body))

        if packet_type == u.PacketType.REQUEST_PROTOCOL_VERSION:
            conn.sendall(header(0, packet_type, 4) + struct.pack("I", VERSION))
        elif packet_type == u.PacketType.REQUEST_CONTROLLER_COUNT:
            conn.sendall(header(0, packet_type, 4)
                         + struct.pack("I", len(DEVICES)))
        elif packet_type == u.PacketType.REQUEST_CONTROLLER_DATA:
            data = DEVICES[device_id].pack(VERSION)
            conn.sendall(header(device_id, packet_type, len(data)) + data)
        elif packet_type == u.PacketType.RGBCONTROLLER_RESIZEZONE:
            zone_id, size = struct.unpack("ii", body)
            z = DEVICES[device_id].zones[zone_id]
            z.num_leds = size
            z.leds = [u.LEDData(name=f"{z.name} {i}", value=0)
                      for i in range(size)]
            z.colors = [u.RGBColor(0, 0, 0) for _ in range(size)]
            dev = DEVICES[device_id]
            dev.leds = [led for zz in dev.zones for led in zz.leds]
            dev.colors = [u.RGBColor(0, 0, 0) for _ in dev.leds]
            print(f"resize: device {device_id} zone {zone_id} -> {size}",
                  flush=True)
        elif packet_type == u.PacketType.RGBCONTROLLER_UPDATEZONELEDS:
            print(f"frame: device {device_id}, {len(body)} bytes", flush=True)
        elif packet_type in (u.PacketType.REQUEST_PROFILE_LIST,
                             u.PacketType.REQUEST_PLUGIN_LIST):
            empty = struct.pack("IH", 6, 0)
            conn.sendall(header(0, packet_type, len(empty)) + empty)


def main():
    port = int(sys.argv[1])
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", port))
    sock.listen(5)
    print(f"fake OpenRGB SDK server on {port}", flush=True)
    while True:
        conn, _ = sock.accept()
        threading.Thread(target=serve, args=(conn,), daemon=True).start()


if __name__ == "__main__":
    main()
