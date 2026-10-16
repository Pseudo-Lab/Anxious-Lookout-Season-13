#!/bin/sh
# Root-only host operation; no cleanup, unmount, formatting or watcher.
set -eu
root=/var/lib/anxious-lookout/user-runners
test "$(id -u)" = 0
test -d "$root"
test "$(readlink -e "$root")" = "$root"
test "$(stat -c '%u:%a' "$root")" = 0:700
if ! mountpoint -q "$root"; then
    mount --bind "$root" "$root"
fi
mount --make-rshared "$root"
findmnt -n -o PROPAGATION --mountpoint "$root" | grep -qw shared
