#!/bin/bash
# PM host only. Fixed read-only metadata collection, NOT a live trial runner.
set -eu
set -o noclobber
umask 077
ulimit -c 0
[ "$#" = 4 ] || exit 1
node_proxy_kubectl=$1
node_proxy_agent=$2
node_proxy_directory=$3
node_proxy_action=$4
case "$node_proxy_kubectl" in /*/kubectl) ;; *) exit 1 ;; esac
[[ "$node_proxy_agent" =~ ^cilium-[a-z0-9]+$ ]] || exit 1
[ -d "$node_proxy_directory" ] && [ ! -L "$node_proxy_directory" ] || exit 1
[ "$(stat -c %a "$node_proxy_directory")" = 700 ] || exit 1
[ "$(stat -c %u "$node_proxy_directory")" = "$(id -u)" ] || exit 1
node_proxy_read() {
  node_proxy_name=$1
  shift
  for node_proxy_suffix in json stderr exit; do
    [ ! -e "$node_proxy_directory/$node_proxy_name.$node_proxy_suffix" ] && [ ! -L "$node_proxy_directory/$node_proxy_name.$node_proxy_suffix" ] || return 1
  done
  node_proxy_exit=0
  (
    ulimit -f 2048
    exec timeout --signal=TERM --kill-after=1s 12s "$node_proxy_kubectl" --request-timeout=8s "$@"
  ) >"$node_proxy_directory/$node_proxy_name.json" 2>"$node_proxy_directory/$node_proxy_name.stderr" || node_proxy_exit=$?
  printf '%s\n' "$node_proxy_exit" >"$node_proxy_directory/$node_proxy_name.exit"
  [ "$node_proxy_exit" = 0 ]
}
case "$node_proxy_action" in
  objects)
    node_proxy_read nodes get --raw /api/v1/nodes
    node_proxy_read crd get --raw /apis/apiextensions.k8s.io/v1/customresourcedefinitions/ciliumnodeconfigs.cilium.io
    node_proxy_read daemonset get --raw /apis/apps/v1/namespaces/kube-system/daemonsets/cilium
    node_proxy_read agents get --raw '/api/v1/namespaces/kube-system/pods?labelSelector=k8s-app%3Dcilium'
    node_proxy_read nodeConfigs get --raw /apis/cilium.io/v2/namespaces/kube-system/ciliumnodeconfigs
    node_proxy_read configMap get --raw /api/v1/namespaces/kube-system/configmaps/cilium-config
    node_proxy_read runnerPolicy get --raw /apis/cilium.io/v2/namespaces/codex-trial/ciliumnetworkpolicies/access-web-boundary-v1
    node_proxy_read apiLink get --raw /apis/cilium.io/v2/namespaces/m2-hosting/ciliumnetworkpolicies/hosting-access-web-link-v1
    node_proxy_read globalPolicy get --raw /apis/cilium.io/v2/ciliumclusterwidenetworkpolicies/anxious-lookout-node-protection
    node_proxy_read services get --raw /api/v1/namespaces/codex-trial/services
    node_proxy_read independentApiServer config view --minify -o 'jsonpath={.clusters[0].cluster.server}'
    ;;
  agent)
    # config get reads the running daemon's config, not the rendered init files.
    node_proxy_read version -n kube-system exec "$node_proxy_agent" -c cilium-agent -- cilium-dbg version
    node_proxy_read status -n kube-system exec "$node_proxy_agent" -c cilium-agent -- cilium-dbg status -o json
    node_proxy_read enable-bpf-tproxy -n kube-system exec "$node_proxy_agent" -c cilium-agent -- cilium-dbg config get EnableBPFTProxy
    node_proxy_read enable-ipv4 -n kube-system exec "$node_proxy_agent" -c cilium-agent -- cilium-dbg config get EnableIPv4
    node_proxy_read enable-ipv6 -n kube-system exec "$node_proxy_agent" -c cilium-agent -- cilium-dbg config get EnableIPv6
    node_proxy_read install-iptables-rules -n kube-system exec "$node_proxy_agent" -c cilium-agent -- cilium-dbg config get InstallIptRules
    node_proxy_read enable-endpoint-routes -n kube-system exec "$node_proxy_agent" -c cilium-agent -- cilium-dbg config get EnableEndpointRoutes
    ;;
  *) exit 1 ;;
esac
printf '{"readonlyCollectionCompleted":true}\n'
