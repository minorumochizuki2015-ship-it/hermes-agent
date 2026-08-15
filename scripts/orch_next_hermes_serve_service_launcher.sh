#!/bin/sh
set -eu

case "$0" in
    /*) ;;
    *) exit 64 ;;
esac

launcher_dir=${0%/*}
launcher_name=${0##*/}
if [ -L "$0" ]; then
    exit 65
fi
physical_dir=$(CDPATH= cd -P "$launcher_dir" && pwd -P)
if [ "$physical_dir/$launcher_name" != "$0" ]; then
    exit 65
fi

root_dir=${physical_dir%/*}
controller_path="$root_dir/scripts/orch_next_hermes_mcp_launcher.py"
controller_sha256="57a016fa737d6120f4ad871ddc77aa561c4d1c752e5948cee9c1094ad7b15267"
if [ ! -f "$controller_path" ] || [ -L "$controller_path" ]; then
    exit 69
fi
cd "$root_dir"
exec /usr/bin/env -i \
    PATH=/usr/bin:/bin:/usr/sbin:/sbin \
    /usr/bin/python3 -I -S -c '
import hashlib
import os
import stat
import sys

path = sys.argv[1]
expected_digest = sys.argv[2]
flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
try:
    descriptor = os.open(path, flags)
except OSError:
    raise SystemExit(69)
try:
    before = os.fstat(descriptor)
    chunks = []
    while True:
        chunk = os.read(descriptor, 1024 * 1024)
        if not chunk:
            break
        chunks.append(chunk)
    after = os.fstat(descriptor)
finally:
    os.close(descriptor)
if (
    not stat.S_ISREG(before.st_mode)
    or before.st_uid != os.getuid()
    or before.st_nlink != 1
    or stat.S_IMODE(before.st_mode) & 0o022
    or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
):
    raise SystemExit(66)
source = b"".join(chunks)
if hashlib.sha256(source).hexdigest() != expected_digest:
    raise SystemExit(66)
code = compile(source, path, "exec", dont_inherit=True)
sys.argv = [path, *sys.argv[3:]]
namespace = {
    "__name__": "__main__",
    "__file__": path,
    "__package__": None,
    "__spec__": None,
}
exec(code, namespace)
' "$controller_path" "$controller_sha256" --orch-lifecycle-service "$@"
