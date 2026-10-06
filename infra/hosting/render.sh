#!/usr/bin/env bash
# Emit only. Never applies resources or initializes data.
set -euo pipefail
: "${WEB_IMAGE:?Set immutable web image reference}"
: "${API_IMAGE:?Set immutable API image reference}"
for image in "$WEB_IMAGE" "$API_IMAGE"; do
  [[ "$image" =~ ^[a-zA-Z0-9._/:-]+@sha256:[0-9a-f]{64}$ ]] || { echo 'Digest-qualified image required' >&2; exit 1; }
done
hosting_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source "$hosting_dir/versions.env"
cat <<YAML
apiVersion: v1
kind: Namespace
metadata:
  name: m2-hosting
  labels:
    pod-security.kubernetes.io/enforce: restricted
    pod-security.kubernetes.io/enforce-version: v1.36
---
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: m2-postgres-retain
provisioner: rancher.io/local-path
reclaimPolicy: Retain
volumeBindingMode: WaitForFirstConsumer
allowVolumeExpansion: false
---
apiVersion: v1
kind: ServiceAccount
metadata:
  name: hosting
  namespace: m2-hosting
automountServiceAccountToken: false
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: hosting-config
  namespace: m2-hosting
data:
  AUTH_ORIGIN: https://m2.invalid
  OAUTH_MODE: disabled
  APP_BASE_PATH: ""
  GITHUB_CLIENT_ID: ""
---
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: postgres
  namespace: m2-hosting
spec:
  serviceName: postgres
  replicas: 1
  persistentVolumeClaimRetentionPolicy:
    whenDeleted: Retain
    whenScaled: Retain
  selector:
    matchLabels: {app: hosting-postgres}
  template:
    metadata:
      labels: {app: hosting-postgres}
    spec:
      serviceAccountName: hosting
      automountServiceAccountToken: false
      securityContext:
        runAsNonRoot: true
        runAsUser: 999
        runAsGroup: 999
        fsGroup: 999
        seccompProfile: {type: RuntimeDefault}
      terminationGracePeriodSeconds: 60
      containers:
        - name: postgres
          image: $POSTGRES_IMAGE
          args: [-c, shared_buffers=128MB, -c, max_connections=30]
          env:
            - {name: POSTGRES_DB, value: hosting}
            - {name: POSTGRES_PASSWORD_FILE, value: /run/secrets/password}
          ports: [{containerPort: 5432, name: postgres}]
          volumeMounts:
            - {name: data, mountPath: /var/lib/postgresql}
            - {name: password, mountPath: /run/secrets, readOnly: true}
            - {name: run, mountPath: /var/run/postgresql}
            - {name: tmp, mountPath: /tmp}
          startupProbe:
            exec: {command: [pg_isready, -U, postgres, -d, hosting]}
            periodSeconds: 2
            failureThreshold: 60
          readinessProbe:
            exec: {command: [pg_isready, -U, postgres, -d, hosting]}
            periodSeconds: 5
          resources:
            requests: {cpu: 100m, memory: 256Mi}
            limits: {cpu: 500m, memory: 512Mi}
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            capabilities: {drop: [ALL]}
      volumes:
        - name: password
          secret:
            secretName: hosting-db-admin
            defaultMode: 0440
            items: [{key: password, path: password}]
        - name: run
          emptyDir: {sizeLimit: 16Mi}
        - name: tmp
          emptyDir: {sizeLimit: 32Mi}
  volumeClaimTemplates:
    - metadata: {name: data}
      spec:
        accessModes: [ReadWriteOnce]
        storageClassName: m2-postgres-retain
        resources:
          requests: {storage: 10Gi}
---
apiVersion: v1
kind: Service
metadata:
  name: postgres
  namespace: m2-hosting
spec:
  selector: {app: hosting-postgres}
  clusterIP: None
  ports: [{name: postgres, port: 5432, targetPort: postgres}]
YAML
deployment() {
  local app=$1 image=$2 cpu=$3 memory=$4 limit_cpu=$5 limit_memory=$6
  cat <<YAML
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: $app
  namespace: m2-hosting
spec:
  replicas: 1
  revisionHistoryLimit: 3
  minReadySeconds: 5
  progressDeadlineSeconds: 300
  strategy:
    rollingUpdate: {maxSurge: 1, maxUnavailable: 0}
  selector:
    matchLabels: {app: hosting-$app}
  template:
    metadata:
      labels: {app: hosting-$app}
    spec:
      serviceAccountName: hosting
      automountServiceAccountToken: false
      securityContext:
        runAsNonRoot: true
        runAsUser: 10001
        runAsGroup: 10001
        fsGroup: 10001
        seccompProfile: {type: RuntimeDefault}
      containers:
        - name: $app
          image: $image
          imagePullPolicy: IfNotPresent
          ports: [{name: http, containerPort: 8080}]
          resources:
            requests: {cpu: $cpu, memory: $memory}
            limits: {cpu: $limit_cpu, memory: $limit_memory}
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            capabilities: {drop: [ALL]}
          volumeMounts:
            - {name: tmp, mountPath: /tmp}
YAML
  if [[ "$app" == api ]]; then
    cat <<'YAML'
            - {name: credentials, mountPath: /run/secrets, readOnly: true}
          envFrom: [{configMapRef: {name: hosting-config}}]
          env:
            - {name: DATABASE_URL_FILE, value: /run/secrets/database-url}
            - {name: AUTH_TRANSACTION_KEY_FILE, value: /run/secrets/transaction-key}
            - {name: GITHUB_CLIENT_SECRET_FILE, value: /run/secrets/github-secret}
          startupProbe:
            exec: {command: [python, -m, app.probe, /healthz]}
            periodSeconds: 2
            failureThreshold: 30
          readinessProbe:
            exec: {command: [python, -m, app.probe, /readyz]}
            periodSeconds: 5
          livenessProbe:
            exec: {command: [python, -m, app.probe, /healthz]}
            periodSeconds: 10
      volumes:
        - name: credentials
          secret:
            secretName: hosting-api
            defaultMode: 0440
        - name: tmp
          emptyDir: {sizeLimit: 32Mi}
YAML
  else
    cat <<'YAML'
          env:
            - {name: PORT, value: "8080"}
            - {name: HOSTNAME, value: "0.0.0.0"}
          startupProbe:
            exec:
              command: [node, -e, "fetch('http://127.0.0.1:8080/version.json').then(r=>process.exit(r.ok?0:1),()=>process.exit(1))"]
            periodSeconds: 2
            failureThreshold: 60
          readinessProbe:
            exec:
              command: [node, -e, "fetch('http://127.0.0.1:8080/version.json').then(r=>process.exit(r.ok?0:1),()=>process.exit(1))"]
            periodSeconds: 5
      volumes:
        - name: tmp
          emptyDir: {sizeLimit: 32Mi}
YAML
  fi
  cat <<YAML
---
apiVersion: v1
kind: Service
metadata:
  name: $app
  namespace: m2-hosting
spec:
  selector: {app: hosting-$app}
  ports: [{name: http, port: 8080, targetPort: http}]
YAML
}
deployment api "$API_IMAGE" 50m 64Mi 250m 192Mi
deployment web "$WEB_IMAGE" 100m 128Mi 500m 512Mi
cat <<'YAML'
---
apiVersion: traefik.io/v1alpha1
kind: IngressRoute
metadata:
  name: hosting
  namespace: m2-hosting
spec:
  entryPoints: [web]
  routes:
    - match: Host(`m2.invalid`) && (Path(`/api`) || PathPrefix(`/api/`))
      kind: Rule
      priority: 20
      services: [{name: api, port: 8080}]
    - match: Host(`m2.invalid`) && PathPrefix(`/`) && !(Path(`/api`) || PathPrefix(`/api/`))
      kind: Rule
      priority: 10
      services: [{name: web, port: 8080}]
---
apiVersion: cilium.io/v2
kind: CiliumNetworkPolicy
metadata:
  name: hosting-boundary
  namespace: m2-hosting
specs:
  - endpointSelector:
      matchLabels: {app: hosting-api}
    enableDefaultDeny: {ingress: true, egress: true}
    ingress:
      - fromEndpoints:
          - matchLabels:
              k8s:io.kubernetes.pod.namespace: kube-system
              k8s:app.kubernetes.io/name: traefik
        toPorts: [{ports: [{port: "8080", protocol: TCP}]}]
    egress:
      - toEndpoints:
          - matchLabels:
              k8s:io.kubernetes.pod.namespace: m2-hosting
              k8s:app: hosting-postgres
        toPorts: [{ports: [{port: "5432", protocol: TCP}]}]
      - toEndpoints:
          - matchLabels:
              k8s:io.kubernetes.pod.namespace: kube-system
              k8s:k8s-app: kube-dns
        toPorts: [{ports: [{port: "53", protocol: UDP}, {port: "53", protocol: TCP}]}]
      - toCIDRSet:
          - cidr: 0.0.0.0/0
            except: [0.0.0.0/8, 10.0.0.0/8, 100.64.0.0/10, 127.0.0.0/8, 169.254.0.0/16, 172.16.0.0/12, 192.0.0.0/24, 192.0.2.0/24, 192.168.0.0/16, 198.18.0.0/15, 198.51.100.0/24, 203.0.113.0/24, 224.0.0.0/4, 240.0.0.0/4]
        toPorts: [{ports: [{port: "443", protocol: TCP}]}]
  - endpointSelector:
      matchLabels: {app: hosting-web}
    enableDefaultDeny: {ingress: true, egress: true}
    ingress:
      - fromEndpoints:
          - matchLabels:
              k8s:io.kubernetes.pod.namespace: kube-system
              k8s:app.kubernetes.io/name: traefik
        toPorts: [{ports: [{port: "8080", protocol: TCP}]}]
    egress: []
  - endpointSelector:
      matchLabels: {app: hosting-postgres}
    enableDefaultDeny: {ingress: true, egress: true}
    ingress:
      - fromEndpoints:
          - matchLabels:
              k8s:io.kubernetes.pod.namespace: m2-hosting
              k8s:app: hosting-api
          - matchLabels:
              k8s:io.kubernetes.pod.namespace: m2-hosting
              k8s:app: hosting-ops
        toPorts: [{ports: [{port: "5432", protocol: TCP}]}]
    egress: []
  - endpointSelector:
      matchLabels: {app: hosting-ops}
    enableDefaultDeny: {ingress: true, egress: true}
    ingress: []
    egress:
      - toEndpoints:
          - matchLabels:
              k8s:io.kubernetes.pod.namespace: m2-hosting
              k8s:app: hosting-postgres
        toPorts: [{ports: [{port: "5432", protocol: TCP}]}]
      - toEndpoints:
          - matchLabels:
              k8s:io.kubernetes.pod.namespace: kube-system
              k8s:k8s-app: kube-dns
        toPorts: [{ports: [{port: "53", protocol: UDP}, {port: "53", protocol: TCP}]}]
YAML
