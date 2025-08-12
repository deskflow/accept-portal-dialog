#!/usr/bin/env python3

import time
import subprocess
import os
import re
import ast
import json
import configparser
from datetime import datetime
from pathlib import Path

YDOTOOL_SOCKET = f"/run/user/{os.getuid()}/.ydotool_socket"

_config = configparser.ConfigParser()

# Warning: The accept dialog functions are the most fragile part!
# It may change in future when the Portal devs change the UI design for the Portal permission dialog.
# e.g. If they add new controls, change the tab order, etc. we'll need to change what keys we press here.


def press_key_sequence(config_section):
    """
    Press a sequence of keys, where each key is a string.
    The sequence can include special items like "<sleep>" to pause for 0.1 seconds.
    """
    for i in range(10):
        sequence_name = f"accept_sequence_{i}"
        if not _config.has_option(config_section, sequence_name):
            break

        sequence = _config.get(config_section, sequence_name)

        if sequence == "<sleep>":
            log(f"Sleep sequence: {config_section} → {sequence_name}: {sequence}")
            log("Sleeping for 0.1 seconds")
            time.sleep(0.1)
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

            log(f"Accepting KDE Portal permission dialog")
            press_key_sequence("kde")


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
        else:
            log(f"GNOME shell eval returned unexpected result: {result}")
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

        log(f"Accepting GNOME Portal permission dialog")
        press_key_sequence("gnome")


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
    else:
        raise RuntimeError(f"Unsupported desktop: {desktop}")


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

    # TODO: Use the XDG_CONFIG_HOME environment variable if set.
    CONFIG_FILE = Path.home() / ".config" / "accept-portal-dialog" / "config.ini"

    CHECK_INTERVAL = 2  # seconds

    YDOTOOL_TAB_KEY = 15
    YDOTOOL_ENTER_KEY = 28
    YDOTOOL_LEFT_ALT_KEY = 56
    YDOTOOL_S_KEY = 31

    if not CONFIG_FILE.parent.exists():
        log(f"Creating config directory: {CONFIG_FILE.parent}")
        CONFIG_FILE.parent.mkdir(parents=True)

    if not CONFIG_FILE.exists():
        log(f"Creating config file: {CONFIG_FILE}")
        _config["program"] = {
            "ydotoold_socket_path": str(YDOTOOL_SOCKET),
            "check_interval": str(CHECK_INTERVAL),
        }

        alt_s = ",".join(map(str, [YDOTOOL_LEFT_ALT_KEY, YDOTOOL_S_KEY]))

        # On KDE, the permission toggle is on by default, so simply accept.
        # Alt+S is perhaps a bit safer than Enter, but won't work for i18n;
        # in this case the user would need to change the config file to use Enter (code 28).
        _config["kde"] = {
            "dialog_titles": ",".join(
                [
                    "Input capture requested",
                    "Remote control requested",
                ]
            ),
            "accept_sequence_0": alt_s,
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
            "accept_sequence_2": alt_s,
        }

        with open(CONFIG_FILE, "w") as f:
            _config.write(f)

    else:
        log(f"Using existing config file: {CONFIG_FILE}")
        _config.read(CONFIG_FILE)


def main():
    config()

    try:
        log("Checking for yDoTool daemon...")
        ensure_ydotoold()

        log("Watching for Portal permission dialogs... Press Ctrl+C to stop.")
        while True:
            accept_dialogs()
            time.sleep(_config.getint("program", "check_interval"))
    except KeyboardInterrupt:
        log("Stopped watching.")


if __name__ == "__main__":
    main()
