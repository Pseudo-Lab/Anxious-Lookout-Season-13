#!/usr/bin/env bash
# Explicit operator migration Job, emitted only. API does not carry this credential.
set -euo pipefail
: "${API_IMAGE:?Set immutable API image}"
[[ "$API_IMAGE" =~ ^[a-zA-Z0-9._/:-]+@sha256:[0-9a-f]{64}$ ]] || exit 1
cat <<YAML
apiVersion: batch/v1
kind: Job
metadata:
  name: auth-alembic-0001
  namespace: m2-hosting
spec:
  backoffLimit: 0
  activeDeadlineSeconds: 120
  template:
    metadata:
      labels: {app: hosting-ops}
    spec:
      restartPolicy: Never
      serviceAccountName: hosting
      automountServiceAccountToken: false
      securityContext:
        runAsNonRoot: true
        runAsUser: 10001
        runAsGroup: 10001
        fsGroup: 10001
        seccompProfile: {type: RuntimeDefault}
      containers:
        - name: migrate
          image: $API_IMAGE
          command: [python, -m, app.migrate, --revision, "0001_auth"]
          env:
            - {name: ADMIN_DATABASE_URL_FILE, value: /run/secrets/database-url}
            - {name: API_DATABASE_PASSWORD_FILE, value: /run/secrets/api-password}
          volumeMounts:
            - {name: credentials, mountPath: /run/secrets, readOnly: true}
            - {name: tmp, mountPath: /tmp}
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            capabilities: {drop: [ALL]}
          resources:
            requests: {cpu: 25m, memory: 48Mi}
            limits: {cpu: 200m, memory: 128Mi}
      volumes:
        - name: credentials
          secret:
            secretName: hosting-ops
            defaultMode: 0440
        - name: tmp
          emptyDir: {sizeLimit: 16Mi}
YAML
