# Operational correction: host preparation reaches PostgreSQL through loopback

The live hosting-boundary CNP allows PostgreSQL ingress only from hosting-api and
hosting-ops Pods in m2-hosting. Host-network Docker is neither. Host DNS also cannot
resolve the cluster-local DB name. Adding CoreDNS alone does not fix connectivity.
Do not widen DB ingress to host/world, impersonate hosting-api labels, or put a root
hostPath preparer in the restricted runner namespace.

Selected candidate: an operator-owned, temporary `kubectl port-forward` bound ONLY
to127.0.0.1. This uses the authenticated API-server/kubelet port-forward path and does
not exercise ordinary Cilium DB ingress. It therefore needs explicit procedure review
before execution; prior bootstrap approval does not cover this path. The preparation
SA remains unchanged and cannot port-forward. Only PM's existing operational kubeconfig
opens the tunnel. No kubeconfig/admin Kubernetes token enters the preparation tool.

## Before retrying

Keep the failed `owner/` output directory and its log untouched. Empty output is not
proof of zero DB writes: migrate can commit before registry entry, and registry entry
can commit before preparation.json is written. The audit below checks actual DB state.
No registry receipt, row or schema is deleted or reset. Root/volume creation and
Kubernetes POSTs occur only after preparation.json is written in this tool version.
Record the failed attempt time and confirm no concurrent preparer is running.

## Concrete procedure for PM review/execution

1. Read the live postgres Service selector and matching Pod name/UID. Verify it is
   the existing m2-hosting database and targets TCP5432, not another namespace or
   replacement Pod. Record only name/UID, not credentials. Select the exact Pod name
   for the command below. Check port25413 is unused; do not kill another listener.
2. In a separate operator terminal, start a foreground tunnel with the existing
   kubeconfig (no background restart loop, no `--address 0.0.0.0`):

   ```sh
   kubectl --kubeconfig "$RUNNER_KUBECONFIG" -n m2-hosting \
     port-forward --address 127.0.0.1 "pod/$RUNNER_POSTGRES_POD" 25413:5432
   ```

   Wait for the loopback forwarding-ready line. If the Pod changes or tunnel dies,
   stop this attempt; do not silently reconnect/retry preparation. Keep tunnel
   stdout separate from input files; it contains no DB URL. Localhost access is
   limited to this host but other host processes can attempt connections while it
   is open; PostgreSQL authentication still applies. Close it immediately after
   audit/preparation, including failures.
3. Use the new helper from this commit inside Docker. It can be mounted read-only
   into the already imported039cc88 API/tool image, avoiding a rebuild solely for
   this correction. Set `RUNNER_SOURCE_BACKEND` to the reviewed backend directory.
   Mount that directory at/source and use PYTHONPATH=/source, overriding packaged
   app/ops code. Original input files remain read-only. Use the same private UID0
   input/output context as preparation. The `route` directory must NOT yet exist:

   ```sh
   docker run --rm --user 0:0 --network none --read-only --cap-drop ALL \
     --security-opt no-new-privileges \
     --mount "type=bind,src=$RUNNER_SOURCE_BACKEND,dst=/source,readonly" \
     --mount "type=bind,src=$RUNNER_INPUT_DIR,dst=/inputs,readonly" \
     --mount "type=bind,src=$RUNNER_OUTPUT_PARENT,dst=/output" \
     -e PYTHONPATH=/source --entrypoint python "$RUNNER_TOOL_IMAGE" \
     -B -m ops.user_runner_db_route stage \
     --source-dir /inputs --output-dir /output/route --port 25413
   ```

   The helper accepts only the expected service host, postgres/anxious_api users,
   psycopg driver, matching DB name, default DB port and no query overrides. It
   preserves user/password/database and changes only host/port to127.0.0.1:25413 in
   new private0600 copies. It does not print URLs or change production Secret files.
   Unexpected URL options require inspection, not bypassing validation.
4. Read-only audit through the tunnel (ADMIN URL copy, selected owner from inputs):

   ```sh
   docker run --rm --user 0:0 --network host --read-only --cap-drop ALL \
     --security-opt no-new-privileges \
     --mount "type=bind,src=$RUNNER_SOURCE_BACKEND,dst=/source,readonly" \
     --mount "type=bind,src=$RUNNER_INPUT_DIR,dst=/inputs,readonly" \
     --mount "type=bind,src=$RUNNER_OUTPUT_PARENT/route,dst=/route,readonly" \
     -e PYTHONPATH=/source --entrypoint python "$RUNNER_TOOL_IMAGE" \
     -B -m ops.user_runner_db_route audit \
     --database-url-file /route/admin-database-url --inputs /inputs/inputs.json
   ```

   Output is only runtimeSchemaPresent/ownerRegistryPresent/ownerEligible/retryAllowed
   booleans. Existing schema is validated; unknown/partial schema errors fail closed.
   No migration is run. Retry only on exit0 with retryAllowed=true AND no concurrent
   attempt. If ownerRegistryPresent=true or the audit fails, preserve everything
   and send the safe result to back for reconciliation; no new output retry yet.
5. If the audit permits retry, obtain a fresh bounded preparer token using the
   reviewed temporary SA/bindings procedure (remove privileges after this attempt).
   Run the README preparation command with these exact changes:
   - additionally mount `RUNNER_OUTPUT_PARENT/route` at/route read-only;
   - mount reviewed backend source at/source read-only, set PYTHONPATH=/source;
   - DATABASE_URL_FILE=/route/database-url;
   - ADMIN_DATABASE_URL_FILE=/route/admin-database-url;
   - use `--output /output/owner-dbroute-1`, never the failed owner path.
   Keep `--network host`; neither DB DNS override nor DB CNP change is needed.
   No automatic retry on failure. Keep both old/new output and logs.
6. Close the foreground tunnel (Ctrl-C), verify127.0.0.1:25413 no longer listens,
   and remove preparation RoleBinding/ClusterRoleBinding per approved teardown.
   Keep private route copies as evidence or remove only those new copies under
   the operational credential cleanup procedure; never delete the original DB URL
   inputs/production Secrets. There is no ongoing port-forward dependency for the
   API: its existing in-cluster database route remains unchanged.

Record the target Pod UID, requested/local port, tunnel start/end, safe audit result,
preparation exit status, new namespace/state/storage evidence, credential teardown
and absence of the listener. This is not proof of model/provider connectivity.
