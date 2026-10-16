#!/usr/bin/env bash
# Issue #13 decision C: retired cluster-wide guards must not be recreated.
# An empty List is deliberately non-mutating; it DOES NOT delete live policies.
# Existing installations must follow DEFAULT-ALLOW.md explicitly.
set -euo pipefail
printf '%s\n' '{"apiVersion":"v1","kind":"List","items":[]}'
