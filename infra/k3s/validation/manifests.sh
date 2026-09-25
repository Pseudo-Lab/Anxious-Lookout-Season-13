#!/usr/bin/env bash
# Emit disposable fixtures only. Root/operator decides when to apply/delete.
set -euo pipefail
namespace() {
  cat <<YAML
---
apiVersion: v1
kind: Namespace
metadata:
  name: $1
  labels:
    pod-security.kubernetes.io/enforce: restricted
    pod-security.kubernetes.io/enforce-version: v1.36
YAML
  if [[ "$1" == tenant-validation-* ]]; then
    echo '    anxious-lookout.io/tenant: "true"'
  fi
}
fixture() {
  local ns=$1 name=$2 app=$3
  cat <<YAML
---
apiVersion: v1
kind: Pod
metadata:
  name: $name
  namespace: $ns
  labels:
    app.kubernetes.io/name: $app
    anxious-lookout.io/validation: issue-1
spec:
  automountServiceAccountToken: false
  securityContext:
    runAsNonRoot: true
    runAsUser: 1000
    runAsGroup: 1000
    seccompProfile:
      type: RuntimeDefault
  containers:
    - name: probe
      image: python:3.12-alpine
      imagePullPolicy: IfNotPresent
      env:
        - name: PYTHONDONTWRITEBYTECODE
          value: "1"
      command: [python, -u, -c]
      args:
        - |
          import http.server, threading
          class Handler(http.server.BaseHTTPRequestHandler):
              def do_GET(self):
                  data = b'infra-validation-ok\\n'
                  self.send_response(200)
                  self.send_header('Content-Length', str(len(data)))
                  self.end_headers()
                  self.wfile.write(data)
              def log_message(self, *_): pass
          for port in (8080, 9090):
              threading.Thread(target=http.server.ThreadingHTTPServer(('0.0.0.0', port), Handler).serve_forever, daemon=True).start()
          threading.Event().wait()
      readinessProbe:
        exec:
          command: [python, -c, "import socket; socket.create_connection(('127.0.0.1',8080),2).close()"]
        initialDelaySeconds: 1
        periodSeconds: 5
      securityContext:
        allowPrivilegeEscalation: false
        readOnlyRootFilesystem: true
        capabilities:
          drop: [ALL]
      resources:
        requests:
          cpu: 20m
          memory: 48Mi
        limits:
          cpu: 200m
          memory: 128Mi
---
apiVersion: v1
kind: Service
metadata:
  name: $name
  namespace: $ns
  labels:
    anxious-lookout.io/validation: issue-1
spec:
  selector:
    app.kubernetes.io/name: $app
    anxious-lookout.io/validation: issue-1
  ports:
    - name: allowed
      port: 8080
      targetPort: 8080
    - name: forbidden
      port: 9090
      targetPort: 9090
YAML
}
namespace tenant-validation-a
namespace tenant-validation-b
namespace validation-control
namespace validation-private
# Leave platform-system labels and lifecycle to the operator. Create it separately
# if absent, so cleanup never removes a production namespace.
fixture tenant-validation-a agent validation-agent
fixture tenant-validation-b agent validation-agent
fixture platform-system validation-materials-api materials-api
fixture platform-system validation-agent-manager agent-manager
fixture validation-control probe validation-probe
fixture validation-private private-api validation-private-api
