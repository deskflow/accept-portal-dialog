#!/usr/bin/env python3

# Deskflow -- mouse and keyboard sharing utility
# SPDX-License-Identifier: GPL-2.0-only WITH LicenseRef-OpenSSL-Exception
# SPDX-FileCopyrightText: 2025 Symless Ltd.

# This script accepts Portal permission dialogs in GNOME and KDE desktops using yDoTool.
#
# Warning: The key sequence configs are the most fragile part!
#
# You may need to edit the config file to change the key sequences for accepting the dialogs,
# especially if you use a different keyboard layout or if the dialog design changes. Portal
# permission dialogs differ slightly in design with things like tab order, especially in GNOME.

import time
import subprocess
import os
import re
import ast
import json
import configparser
from datetime import datetime
from pathlib import Path

_config = configparser.ConfigParser()


def press_key_sequence(config_section):
    for i in range(10):
        sequence_name = f"accept_sequence_{i}"
        if not _config.has_option(config_section, sequence_name):
            break

        sequence = _config.get(config_section, sequence_name)

        if sequence == "<sleep>":
            log(f"Sleep sequence: {config_section} → {sequence_name}: {sequence}")
            SEQUENCE_KEY_SLEEP = _config.getfloat("program", "sequence_key_sleep")
            log(f"Sleeping for {SEQUENCE_KEY_SLEEP} seconds")
            time.sleep(SEQUENCE_KEY_SLEEP)
            continue

        log(f"Pressing key sequence {config_section} → {sequence_name}: {sequence}")
        press_keys(*sequence.split(","))


def kde_search_window(title):
    out, _ = run("kdotool", "search", "--name", title)
    return [wid for wid in out.splitlines() if wid] if out else []


def kde_get_active_window():
    out, _ = run("kdotool", "getactivewindow")
    return out.strip() if out else None


def press_keys(*keys):
    down = [f"{key}:1" for key in keys]
    up = [f"{key}:0" for key in reversed(keys)]
    log(f"Pressing down keys: {down}, up keys: {up}")
    run("ydotool", "key", *down, *up)


def kde_ensure_window_focus(window_id):
    active_window = kde_get_active_window()
    if active_window != window_id:
        log(f"Activating window: {window_id}")
        run("kdotool", "windowactivate", window_id)


def kde_find_and_accept():
    for title in _config.get("kde", "dialog_titles").split(","):
        for window_id in kde_search_window(title):
            kde_ensure_window_focus(window_id)

            sleep_before_sequence()

            log(f"Accepting KDE Portal permission dialog")
            press_key_sequence("kde")


# TODO: Use Python dbus instead of gdbus.
def gnome_shell_eval(value):
    try:
        out = subprocess.check_output(
            [
                "gdbus",
                "call",
                "--session",
                "--dest",
                "org.gnome.Shell",
                "--object-path",
                "/org/gnome/Shell",
                "--method",
                "org.gnome.Shell.Eval",
                f"string:{value}",
            ],
            text=True,
        )
        out = out.replace("(true,", "(True,").replace("(false,", "(False,")
        ok, data = ast.literal_eval(out)
        result = decode_eval_json(data)
        return ok, result
    except FileNotFoundError:
        print("Program 'gdbus' not found")
        return False
    except subprocess.CalledProcessError as e:
        print("DBus call failed:", e)
        return False


def gnome_check_shell_eval():
    try:
        ok, result = gnome_shell_eval("1+1")
        if ok and result == 2:
            return True
        elif result:
            log(f"GNOME shell eval returned unexpected result: {result}")
            return False
        else:
            log("GNOME shell eval returned no result")
            return False
    except Exception as e:
        log(f"Error checking GNOME shell eval: {e}")
        return False


def decode_eval_json(s):
    obj = s
    while isinstance(obj, str):
        try:
            nxt = json.loads(obj)
        except json.JSONDecodeError:
            break
        if nxt is obj or nxt == obj:
            break
        obj = nxt
    return obj


def gnome_activate_window(window_id):
    ok, result = gnome_shell_eval(
        f"""
    global.get_window_actors()
        .find(w => w.meta_window?.get_id() == {window_id})
        ?.meta_window?.activate(global.get_current_time());
    """
    )

    if not ok:
        raise RuntimeError(f"Failed to activate GNOME dialog: {window_id}")


def gnome_find_and_accept():
    if not gnome_check_shell_eval():
        print(
            "\nHint: Enable GNOME unsafe mode to use shell eval:"
            "\n    Alt+F2 → lg → global.context.unsafe_mode = true\n"
        )
        raise RuntimeError(
            "Unable to use shell eval, check GNOME unsafe mode is enabled."
        )

    for title in _config.get("gnome", "dialog_titles").split(","):
        ok, result = gnome_shell_eval(
            f"""
        JSON.stringify(
            global.get_window_actors()
                .map(w => ({{
                    title: w.meta_window?.get_title(),
                    id: w.meta_window?.get_id(),
                    focus: w.meta_window?.has_focus(),
                }}))
                .filter(w => w.title && w.title.includes({json.dumps(title)}))
        )
        """
        )

        if not ok or not result:
            continue

        if len(result) == 0:
            continue

        window = result[0]
        title = window["title"]
        id = window["id"]
        focus = window["focus"]
        log(
            f"Found GNOME dialog: {title} (ID: {id}, Focus: {"yes" if focus else "no"})"
        )

        if not focus:
            log(f"Activating GNOME dialog: {title}")
            gnome_activate_window(id)

        sleep_before_sequence()

        log(f"Accepting GNOME Portal permission dialog")
        press_key_sequence("gnome")


def sleep_before_sequence():
    SEQUENCE_START_DELAY = _config.getfloat("program", "sequence_start_delay")
    log(f"Waiting {SEQUENCE_START_DELAY} seconds before accepting dialog")
    time.sleep(SEQUENCE_START_DELAY)


def ensure_ydotoold():
    YDOTOOL_SOCKET = _config.get("program", "ydotoold_socket_path")
    sock_path = Path(YDOTOOL_SOCKET)
    if sock_path.exists():
        log(f"Found yDoTool daemon, socket: {sock_path}")
        _, code = run("ydotool", "debug")
        if code != 0:
            raise RuntimeError(f"yDoTool error, try deleting socket: {sock_path}")

        return

    log("Starting yDoTool daemon...")
    try:
        subprocess.run(
            [
                "sudo",
                "-b",
                "ydotoold",
                "--socket-path",
                YDOTOOL_SOCKET,
                f"--socket-own={os.getuid()}:{os.getgid()}",
            ],
            check=True,
        )
    except subprocess.CalledProcessError as e:
        log(f"Failed to start yDoTool daemon, check if it's installed")
        log(f"Error: {e}")
        exit(1)

    while not sock_path.exists():
        log("Waiting for yDoTool daemon to start...")
        time.sleep(1)


def accept_dialogs():
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "")
    if "GNOME" in desktop:
        gnome_find_and_accept()
    elif "KDE" in desktop:
        kde_find_and_accept()
    elif desktop:
        raise RuntimeError(f"Unsupported desktop: {desktop}")
    else:
        raise RuntimeError("XDG_CURRENT_DESKTOP environment variable is not set.")


def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}")


def run(*args):
    try:
        p = subprocess.run(
            args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
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
        log(f"Command not found: {args[0]}")
        exit(1)


def config():

    # TODO: Use existing YDOTOOL_SOCKET env var if set.
    YDOTOOL_SOCKET = f"/run/user/{os.getuid()}/.ydotool_socket"

    # TODO: Use the XDG_CONFIG_HOME env var if set.
    CONFIG_FILE = Path.home() / ".config" / "accept-portal-dialog" / "config.ini"

    # Check very frequently for new dialogs to start the sequence delay as soon as possible.
    CHECK_INTERVAL = 0.5

    # Wait after detecting a dialog before starting the key sequence.
    # This is to allow the user to see the dialog to reduce the security risk.
    # Any longer, and the user will likely get impatient and click the dialog manually.
    SEQUENCE_START_DELAY = 0.5

    # How long to wait between key presses in the sequence when <sleep> is used.
    # This is to allow the GUI to animate properly, especially in GNOME.
    SEQUENCE_KEY_SLEEP = 0.5

    YDOTOOL_TAB_KEY = 15
    YDOTOOL_ENTER_KEY = 28
    YDOTOOL_LEFT_ALT_KEY = 56
    YDOTOOL_S_KEY = 31

    ALT_S_SEQUENCE = ",".join(map(str, [YDOTOOL_LEFT_ALT_KEY, YDOTOOL_S_KEY]))

    if not CONFIG_FILE.parent.exists():
        log(f"Creating config directory: {CONFIG_FILE.parent}")
        CONFIG_FILE.parent.mkdir(parents=True)

    if not CONFIG_FILE.exists():
        log(f"Creating config file: {CONFIG_FILE}")
        _config["program"] = {
            "verbose_logging": "false",
            "ydotoold_socket_path": str(YDOTOOL_SOCKET),
            "check_interval": str(CHECK_INTERVAL),
            "sequence_start_delay": str(SEQUENCE_START_DELAY),
            "sequence_key_sleep": str(SEQUENCE_KEY_SLEEP),
        }

        # On KDE, the permission toggle is on by default, so simply accept.
        _config["kde"] = {
            "dialog_titles": ",".join(
                [
                    "Input capture requested",
                    "Remote control requested",
                ]
            ),
            "accept_sequence_0": YDOTOOL_ENTER_KEY,
        }

        # GNOME takes a split second to enable the dialog accept button.
        # Different dialogs seem to have different tab orders, so use Alt+S which
        # is the common shortcut for "Accept" in GNOME dialogs.
        _config["gnome"] = {
            "dialog_titles": ",".join(
                [
                    "Capture Input",
                    "Remote Desktop",
                ]
            ),
            "accept_sequence_0": YDOTOOL_ENTER_KEY,
            "accept_sequence_1": "<sleep>",
            "accept_sequence_2": ALT_S_SEQUENCE,
        }

        with open(CONFIG_FILE, "w") as f:
            _config.write(f)

    else:
        log(f"Using existing config file: {CONFIG_FILE}")
        _config.read(CONFIG_FILE)
        for key in ["sequence_key_sleep", "check_interval", "sequence_start_delay"]:

            # Any less than 0.5 seconds is likely too low for GUI animations to complete.
            MIN_SLEEP = 0.5
            if _config.getfloat("program", key) <= MIN_SLEEP:
                value = _config.getfloat("program", key)
                log(
                    f"Warning: {key} value {value} is below recommended min: {MIN_SLEEP}"
                )
                continue


# TODO: Use Python dbus instead of gdbus.
def is_gnome_screen_locked():
    try:
        out = subprocess.check_output(
            [
                "gdbus",
                "call",
                "--session",
                "--dest",
                "org.gnome.ScreenSaver",
                "--object-path",
                "/org/gnome/ScreenSaver",
                "--method",
                "org.gnome.ScreenSaver.GetActive",
            ],
            text=True,
        )
        # Output: '(true,)' or '(false,)'
        return out.strip().startswith("(true")
    except Exception as e:
        raise RuntimeError("Could not check GNOME lock screen") from e


def is_kde_screen_locked():
    try:
        import dbus

    except ImportError:
        raise RuntimeError("dbus-python is needed for KDE lock detection.")

    try:
        bus = dbus.SessionBus()
        service = bus.get_object(
            "org.freedesktop.ScreenSaver", "/org/freedesktop/ScreenSaver"
        )
        iface = dbus.Interface(service, "org.freedesktop.ScreenSaver")
        locked = iface.GetActive()
        return locked
    except Exception as e:
        raise RuntimeError("Could not check KDE lock screen") from e


def is_desktop_locked():
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "")
    if "GNOME" in desktop:
        return is_gnome_screen_locked()
    elif "KDE" in desktop:
        return is_kde_screen_locked()
    elif desktop:
        raise RuntimeError(f"Unsupported desktop: {desktop}")
    else:
        raise RuntimeError("XDG_CURRENT_DESKTOP environment variable is not set.")


def is_verbose():
    return _config.getboolean("program", "verbose_logging", fallback=False)


def main():
    config()

    try:
        log("Checking for yDoTool daemon...")
        ensure_ydotoold()

        log("Watching for Portal permission dialogs... Press Ctrl+C to stop.")
        while True:
            if not is_desktop_locked():
                accept_dialogs()
            elif is_verbose():
                log("Desktop is locked, skipping dialog checks.")

            time.sleep(_config.getfloat("program", "check_interval"))
    except KeyboardInterrupt:
        log("Stopped watching.")


if __name__ == "__main__":
    main()
