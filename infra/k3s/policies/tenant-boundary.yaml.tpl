# Render with ../render-policy.sh; do not apply this template directly.
apiVersion: cilium.io/v2
kind: CiliumClusterwideNetworkPolicy
metadata:
  name: anxious-lookout-tenant-boundary
spec:
  endpointSelector:
    matchLabels:
      k8s:io.cilium.k8s.namespace.labels.anxious-lookout.io/tenant: "true"
  enableDefaultDeny:
    ingress: true
    egress: true
  ingress:
    - fromEndpoints:
        - matchLabels:
            k8s:io.kubernetes.pod.namespace: platform-system
            k8s:app.kubernetes.io/name: agent-manager
      toPorts:
        - ports:
            - port: "8080"
              protocol: TCP
  egress:
    - toEndpoints:
        - matchLabels:
            k8s:io.kubernetes.pod.namespace: kube-system
            k8s:k8s-app: kube-dns
      toPorts:
        - ports:
            - port: "53"
              protocol: UDP
            - port: "53"
              protocol: TCP
    - toEndpoints:
        - matchLabels:
            k8s:io.kubernetes.pod.namespace: platform-system
            k8s:app.kubernetes.io/name: materials-api
      toPorts:
        - ports:
            - port: "8080"
              protocol: TCP
    # Internet is unrestricted by protocol/port. Cluster endpoint identities
    # are separately controlled above, and internal/unroutable CIDRs excluded.
    - toCIDRSet:
        - cidr: 0.0.0.0/0
          except:
            - 0.0.0.0/8
            - 10.0.0.0/8
            - 100.64.0.0/10
            - 127.0.0.0/8
            - 169.254.0.0/16
            - 172.16.0.0/12
            - 192.0.0.0/24
            - 192.0.2.0/24
            - 192.168.0.0/16
            - 198.18.0.0/15
            - 198.51.100.0/24
            - 203.0.113.0/24
            - 224.0.0.0/4
            - 240.0.0.0/4
            - __NODE_PUBLIC_IPV4__/32
  # Deny takes precedence over future allow rules. Node identities and OCI
  # public-IP NAT must not provide a path into host/Docker/admin services.
  egressDeny:
    - toEntities:
        - host
        - remote-node
        - kube-apiserver
    - toCIDR:
        - 169.254.0.0/16
        - __NODE_PUBLIC_IPV4__/32
