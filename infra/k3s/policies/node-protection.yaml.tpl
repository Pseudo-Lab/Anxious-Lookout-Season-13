# Render the public address before applying. This identity rule is independent
# of tenant labels and source IPs, including addresses recycled from system Pods.
apiVersion: cilium.io/v2
kind: CiliumClusterwideNetworkPolicy
metadata:
  name: anxious-lookout-node-protection
spec:
  endpointSelector:
    matchExpressions:
      - key: k8s:io.kubernetes.pod.namespace
        operator: Exists
      - key: k8s:io.kubernetes.pod.namespace
        operator: NotIn
        values: [kube-system]
  enableDefaultDeny:
    ingress: false
    egress: false
  egressDeny:
    - toEntities:
        - host
        - remote-node
        - kube-apiserver
    - toCIDR:
        - 169.254.0.0/16
        - __NODE_PUBLIC_IPV4__/32
