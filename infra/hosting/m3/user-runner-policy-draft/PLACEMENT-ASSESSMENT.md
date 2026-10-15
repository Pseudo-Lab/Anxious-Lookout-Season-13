# Host-entity diagnostic caller assessment

Decision proposal: a host-network diagnostic container is not individually
identifiable by `fromEntities: [host]`. The destination runner SA and TCP8080
selector limits where traffic goes, but the source covers host identity, not a
single approved container/process. A Kubernetes SA bearer used for API calls does
not turn that host source into a Cilium workload service-account identity.

A host-entity route can therefore only be offered as a **host-trusted transport
rehearsal**, not evidence of caller-specific network isolation. Synthetic bearer
and state-ID checks remain necessary; other host processes must not have token
access. The alternative is a dedicated controller Pod/namespace with an endpoint
identity, isolated DB egress and narrow peer selectors. These are different trust
boundaries; PM must take the tradeoff to the user before selection.

Before concluding that even the host-trusted option works, PM must read the
actual Cilium local-host/host-firewall configuration and observe source identity
and accepted/denied traffic in the approved test window. `allow-localhost` behavior
can bypass the expected endpoint ingress restriction; host firewall enablement
alone is not proof of the runner's policy behavior. No broad host rule is added
here, and no Cilium/source identity has been measured by backend.

Required evidence for a host-trusted proposal: effective local-host setting,
Cilium endpoint identities and policy revisions; diagnostic host-network source
classification; TCP8080 success; other ports and non-host sources denied where
claimed; correct synthetic bearer/state success and wrong values rejected. Host
access to other runner state/token files is a separate host trust boundary, not
solved by this network selector. Existing codex-trial policies are not changed.

Conclusion: **not suitable for the narrow individual-caller isolation claim**.
Conditionally viable for the smaller host-trusted single-runner transport test,
only after explicit choice and observed Cilium behavior. Keep both options closed
until that choice; do not add world/host allowances to make an unexpected denial
pass. This document is source reasoning, not a packet test or execution approval.

References: [Cilium entity and localhost policy](https://docs.cilium.io/en/stable/security/policy/layer3/),
[Cilium host firewall](https://docs.cilium.io/en/stable/security/host-firewall/).
