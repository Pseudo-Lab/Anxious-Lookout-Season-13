#!/bin/sh
# #68: build the anxious_runner_bwrap module packages from the sources in this directory, in an Oracle Linux 9
# container with the same checkpolicy/libsepol (3.6) as the nodes. Reproducible: the same sources give the same
# .pp bytes (checked 2026-11-05). Writes <out>/anxious_runner_bwrap-1.pp and -2.pp and prints their SHA-256;
# vm/selinux-module.sh pins those hashes. No macros (no selinux-policy-devel): the module adds exactly the
# allow rules in its .te file.
#
#   infra/hosting/m3/native-shell/selinux/build.sh <output dir>
set -eu
here=$(cd -- "$(dirname -- "$0")" && pwd)
out=${1:?output dir}
[ ! -e "$out" ] || { echo "refuse: $out exists"; exit 3; }
mkdir -p "$out/v1" "$out/v2"
cp "$here/anxious_runner_bwrap-1.te" "$out/v1/anxious_runner_bwrap.te"
cp "$here/anxious_runner_bwrap-2.te" "$out/v2/anxious_runner_bwrap.te"
docker run --rm --network host -v "$out:/m" oraclelinux:9 sh -ec '
  dnf -q -y install checkpolicy policycoreutils >/dev/null 2>&1
  rpm -q checkpolicy libsepol policycoreutils
  for v in v1 v2; do
    cd /m/$v
    checkmodule -M -m -o anxious_runner_bwrap.mod anxious_runner_bwrap.te
    semodule_package -o anxious_runner_bwrap.pp -m anxious_runner_bwrap.mod
  done'
cp "$out/v1/anxious_runner_bwrap.pp" "$out/anxious_runner_bwrap-1.pp"
cp "$out/v2/anxious_runner_bwrap.pp" "$out/anxious_runner_bwrap-2.pp"
cd "$out" && sha256sum anxious_runner_bwrap-1.pp anxious_runner_bwrap-2.pp
