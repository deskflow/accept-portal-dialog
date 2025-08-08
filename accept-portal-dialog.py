#!/usr/bin/env python3

import time
import subprocess
import os
from datetime import datetime
from pathlib import Path

TARGET_TITLES = [
    "Input capture requested",
    "Remote control requested"
]

CHECK_INTERVAL = 1  # secs
YDOTOOL_SOCKET = f"/run/user/{os.getuid()}/.ydotool_socket"

def accept_dialog(window_id):
    log(f"Accepting Portal permission dialog {window_id}")

    # Warning: This is the most fragile part!
    # It may change in future when the Portal devs change the UI design for the Portal permission dialog.
    # e.g. If they add new controls, change the tab order, etc. we'll need to change what keys we press here.
    tab = 15
    enter = 28
    press_key(window_id, tab)
    press_key(window_id, tab)
    press_key(window_id, enter)

def find_windows_by_title(title):
    out, _ = run("kdotool", "search", "--name", title)
    return [wid for wid in out.splitlines() if wid] if out else []

def get_active_window():
    out, _ = run("kdotool", "getactivewindow")
    return out.strip() if out else None

def press_key(window_id, key):
    log(f"Pressing key {key} on window {window_id}")
    run("ydotool", "key", f"{key}:1", f"{key}:0")

def activate_window(window_id):
    active_window = get_active_window()
    if active_window != window_id:
        log(f"Activating window: {window_id}")
        run("kdotool", "windowactivate", window_id)

def check_windows():
    for title in TARGET_TITLES:
        for window_id in find_windows_by_title(title):
            activate_window(window_id)
            accept_dialog(window_id)


def ensure_ydotoold():
    # TOOD: Use existing YDOTOOL_SOCKET env var if set.
    sock_path = Path(YDOTOOL_SOCKET)
    if sock_path.exists():
        log(f"Found yDoTool daemon, socket: {sock_path}")
        _, code = run("ydotool", "debug")
        if code != 0:
            raise RuntimeError(f"yDoTool error, try deleting socket: {sock_path}")

        return
    
    log("Starting yDoTool daemon...")
    subprocess.run(
        ["sudo", "-b", "ydotoold",
        "--socket-path", YDOTOOL_SOCKET,
         f"--socket-own={os.getuid()}:{os.getgid()}"],
        check=True
    )

    while not sock_path.exists():
        log("Waiting for yDoTool daemon to start...")
        time.sleep(1)


def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}")

def run(*args):
    try:
        p = subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )

        if p.returncode != 0:
            if p.stderr:
                log(f"Error running {args[0]}, code {p.returncode}: {p.stderr}")
                return p.stderr, p.returncode
            else:
                log(f"Error running {args[0]}, code {p.returncode}")
                return None, p.returncode

        return p.stdout, 0
    except FileNotFoundError:
        log("Error: kdotool is not installed or not in PATH.")
        exit(1)

def main():
    log("Watching for Portal permission dialogs... Press Ctrl+C to stop.")

    try:
        ensure_ydotoold()
        while True:
            check_windows()
            time.sleep(CHECK_INTERVAL)
    except KeyboardInterrupt:
        log("Stopped watching.")


if __name__ == "__main__":
    main()
