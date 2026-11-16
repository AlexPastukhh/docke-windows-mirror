"""Private Windows handshake helper. No Git command before Job assignment.

Only its parent owns the anonymous stdin pipe. It passes fixed, trusted argv;
this is not an HTTP command endpoint. EOF before GO never launches a command.
"""
import json
import os
import sys
import subprocess


def main():
    frame = sys.stdin.buffer.readline(4097)
    if len(frame) > 4096 or not frame.endswith(b'\n'):
        return 111
    try:
        value = json.loads(frame)
    except (ValueError, UnicodeError):
        return 112
    if (type(value) is not dict or set(value) != {'protocol', 'argv'} or
            value['protocol'] != 'FORTIS_COMMAND_V1' or type(value['argv']) is not list or
            not 1 <= len(value['argv']) <= 128 or
            any(type(x) is not str or '\0' in x for x in value['argv'])):
        return 112
    null = os.open(os.devnull, os.O_RDONLY)
    os.dup2(null, 0)
    os.close(null)
    try:
        # Windows has no Unix-style process replacement. Keep this leader
        # alive until the actual command exits and propagate its real status.
        process = subprocess.Popen(value['argv'], stdin=subprocess.DEVNULL,
                                   close_fds=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        return process.wait()
    except OSError:
        return 113


if __name__ == '__main__':
    raise SystemExit(main())
