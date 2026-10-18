#!/usr/bin/env python3
"""Android TV Remote (the protocol behind the Google TV phone remote) from the Mac, over Wi-Fi.

Needs no ADB and no Developer options, only a TV on the same network. It sends remote key presses,
launches apps and types text. It cannot install apps or disable packages (that needs tools/tv.py).

Setup (Python 3.11+, separate from the ADB venv):
    python3.11 -m venv tools/venv-remote && tools/venv-remote/bin/pip install -r tools/requirements-remote.txt

Usage, with TV_HOST set to the TV's IP (or --host):
    remote.py discover                find Android TVs on the network (mDNS)
    remote.py pair                    pair once; the TV shows a code to type here
    remote.py status                  model, power state, foreground app
    remote.py key up up ok home       send key presses (names below, or any KEYCODE_ name)
    remote.py app com.netflix.ninja   launch an app by package name or deep link
    remote.py text "hello"            type into a focused text field
    remote.py live                    use the Mac keyboard as the remote
"""
import argparse
import asyncio
import os
import sys
import time

from androidtvremote2 import AndroidTVRemote, CannotConnect, ConnectionClosed, InvalidAuth

CERTDIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "remote-cert")
CERT = os.path.join(CERTDIR, "cert.pem")
KEY = os.path.join(CERTDIR, "key.pem")
CLIENT_NAME = "tv-vibehack"

KEYS = {
    "up": "DPAD_UP", "down": "DPAD_DOWN", "left": "DPAD_LEFT", "right": "DPAD_RIGHT",
    "ok": "DPAD_CENTER", "enter": "DPAD_CENTER", "select": "DPAD_CENTER",
    "back": "BACK", "home": "HOME", "menu": "MENU", "settings": "SETTINGS", "input": "TV_INPUT",
    "power": "POWER", "mute": "VOLUME_MUTE", "volup": "VOLUME_UP", "voldown": "VOLUME_DOWN",
    "playpause": "MEDIA_PLAY_PAUSE", "chup": "CHANNEL_UP", "chdown": "CHANNEL_DOWN",
}

# Keys in `live` mode. Arrow keys arrive as three-byte escape sequences.
LIVE_KEYS = {
    b"\x1b[A": "DPAD_UP", b"\x1b[B": "DPAD_DOWN", b"\x1b[C": "DPAD_RIGHT", b"\x1b[D": "DPAD_LEFT",
    b"\r": "DPAD_CENTER", b"\n": "DPAD_CENTER", b"\x7f": "BACK", b"\x1b": "BACK",
    b"h": "HOME", b"s": "SETTINGS", b"i": "TV_INPUT", b"p": "POWER", b" ": "MEDIA_PLAY_PAUSE",
    b"+": "VOLUME_UP", b"=": "VOLUME_UP", b"-": "VOLUME_DOWN", b"m": "VOLUME_MUTE",
}
LIVE_HELP = ("Mac keyboard remote:  arrows = D-pad   Enter = OK   Backspace/Esc = Back   h = Home\n"
             "                      s = Settings   i = Input   + / - = Volume   m = Mute   p = Power\n"
             "                      Space = Play/Pause   q = quit")

HOST = None


def need_host():
    if not HOST:
        sys.exit("Set TV_HOST to your TV's IP address, e.g.  TV_HOST=192.168.1.50 tools/remote.py status\n"
                 "(run `remote.py discover` to find it)")
    return HOST


def make_client():
    os.makedirs(CERTDIR, exist_ok=True)
    return AndroidTVRemote(CLIENT_NAME, CERT, KEY, need_host())


async def connect():
    remote = make_client()
    await remote.async_generate_cert_if_missing()
    try:
        await remote.async_connect()
    except InvalidAuth:
        sys.exit("Not paired with this TV yet. Run: remote.py pair")
    except (CannotConnect, OSError, asyncio.TimeoutError) as e:
        sys.exit(f"Can't reach {HOST}:6466 ({e or type(e).__name__}). "
                 "Is the TV on, on the same Wi-Fi, and is the IP right?")
    return remote


def resolve(name):
    return name if ":" in name else KEYS.get(name.lower(), name.upper())


# ---- commands ----------------------------------------------------------------------------------

def discover(seconds):
    from zeroconf import ServiceBrowser, Zeroconf

    service = "_androidtvremote2._tcp.local."
    found = {}

    class Listener:
        def add_service(self, zc, type_, name):
            info = zc.get_service_info(type_, name, timeout=3000)
            if info:
                found[name] = (info.parsed_addresses(), info.port)

        def update_service(self, zc, type_, name):
            pass

        def remove_service(self, zc, type_, name):
            pass

    zc = Zeroconf()
    ServiceBrowser(zc, service, Listener())
    time.sleep(seconds)
    zc.close()
    if not found:
        print("No Android TV found. Check the TV is on and on this Wi-Fi; if macOS asked for Local Network\n"
              "access, allow it. You can also read the IP under the TV's Settings → Network.")
        return
    for name, (addrs, port) in sorted(found.items()):
        print(f"{name.replace('.' + service, '')}  {', '.join(addrs)}  port {port}")


async def pair():
    remote = make_client()
    await remote.async_generate_cert_if_missing()
    try:
        name, mac = await remote.async_get_name_and_mac()
        print(f"Pairing with {name} ({mac}). A code is about to appear on the TV.")
        await remote.async_start_pairing()
    except (CannotConnect, OSError, asyncio.TimeoutError) as e:
        sys.exit(f"Can't reach {HOST}:6467 ({e or type(e).__name__}). Is the TV on and the IP right?")
    while True:
        code = input("Code shown on the TV: ").strip()
        try:
            await remote.async_finish_pairing(code)
            break
        except InvalidAuth:
            print("Wrong code, try again.")
        except ConnectionClosed:
            sys.exit("The TV closed the pairing session. Run `remote.py pair` again.")
    print("Paired. Try: remote.py key home")


async def status():
    remote = await connect()
    await asyncio.sleep(1)   # power state and foreground app arrive just after connecting
    print("device:", remote.device_info)
    print("on:", remote.is_on, "  app:", remote.current_app)
    remote.disconnect()


async def keys(names, delay):
    remote = await connect()
    try:
        for n in names:
            remote.send_key_command(resolve(n))
            await asyncio.sleep(delay)
        await asyncio.sleep(0.3)   # let the last key flush before closing
    except ValueError:
        sys.exit(f"Unknown key '{n}'. Names: {', '.join(KEYS)}, or any KEYCODE_ name.")
    finally:
        remote.disconnect()


async def launch(target):
    remote = await connect()
    remote.send_launch_app_command(target)
    await asyncio.sleep(0.5)
    remote.disconnect()


async def type_text(text):
    remote = await connect()
    remote.send_text(text)
    await asyncio.sleep(0.5)
    remote.disconnect()


async def live():
    import termios
    import tty

    remote = await connect()
    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)
    loop = asyncio.get_running_loop()
    done = asyncio.Event()

    def on_input():
        data = os.read(fd, 8)
        if data in (b"q", b"Q", b"\x04"):
            done.set()
            return
        key = LIVE_KEYS.get(data)
        if key:
            try:
                remote.send_key_command(key)
            except ConnectionClosed:
                print("\r\nConnection to the TV closed.")
                done.set()

    print(LIVE_HELP)
    tty.setcbreak(fd)
    loop.add_reader(fd, on_input)
    try:
        await done.wait()
    finally:
        loop.remove_reader(fd)
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)
        remote.disconnect()


def main():
    global HOST
    ap = argparse.ArgumentParser(description="Android TV remote over Wi-Fi", usage="remote.py [--host IP] command ...")
    ap.add_argument("--host", default=os.environ.get("TV_HOST"), help="TV IP address (default: $TV_HOST)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("discover", help="find Android TVs on the network")
    d.add_argument("--seconds", type=float, default=5)
    sub.add_parser("pair", help="pair with the TV (asks for the code shown on screen)")
    sub.add_parser("status", help="show model, power state and foreground app")
    k = sub.add_parser("key", help="send key presses")
    k.add_argument("names", nargs="+")
    k.add_argument("--delay", type=float, default=0.25, help="seconds between keys")
    a = sub.add_parser("app", help="launch an app by package name or deep link")
    a.add_argument("target")
    t = sub.add_parser("text", help="type text into the focused field")
    t.add_argument("text")
    sub.add_parser("live", help="use the Mac keyboard as the remote")
    args = ap.parse_args()
    HOST = args.host

    if args.cmd == "discover":
        discover(args.seconds)
        return
    coro = {
        "pair": lambda: pair(),
        "status": lambda: status(),
        "key": lambda: keys(args.names, args.delay),
        "app": lambda: launch(args.target),
        "text": lambda: type_text(args.text),
        "live": lambda: live(),
    }[args.cmd]()
    try:
        asyncio.run(coro)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
