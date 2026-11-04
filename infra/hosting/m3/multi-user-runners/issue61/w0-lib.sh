# #61 W0-only helpers (sourced by w0-baseline.sh after lib.sh). Kept apart from lib.sh, whose SHA-256 is part
# of W1's clearance. Read-only.
# check_imports: one line per image; ctr_has rc 0 = imported, 1 = looked up and absent, anything else = the
# lookup failed, which counts as a failed lookup (never as "not imported").
check_imports() {
  for ref in $IMAGE_REFS; do
    ctr_has "$ref"; r=$?
    case "$r" in
      0) note "imported $ref";;
      1) note "not imported $ref";;
      *) fail_lookup "containerd image list (rc $r) for $ref";;
    esac
  done
}
