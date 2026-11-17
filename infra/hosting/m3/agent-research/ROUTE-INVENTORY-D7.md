# D7 repository route inventory

Decision: [D7](https://github.com/Pseudo-Lab/Anxious-Lookout-Season-13/issues/97#issuecomment-6934762282).
Repository/source review only; no new dev/production queries or policy changes.
The subsequent PM direction permits source/dev preparation now and requires the
production operator to finalize its actual routes, middleware and Service inputs.

| Route family / source | Entry and destinations | Access boundary / state |
| --- | --- | --- |
| `m2-hosting/hosting`, `infra/hosting/render.sh` | web80; exact configured IPv4 Host; API8080 under exact `/api` or `/api/`, excluding the `/api/internal` prefix; other root paths to web8080 except Android | Dev renderer. No source-IP allowlist emitted. This is not the production HTTPS configuration. |
| `hosting-root-https-v1`, `https_prepare.py:desired` | websecure; original public IPv4 Host; API8080 and root web8080; internal/fixture/Android/ACME exclusions | Production P1 source and PARITY historical observation. Seven encoded-character refusals. Public human authentication remains application-owned. No source-IP allowlist emitted. |
| `hosting-root-tls-probe-v1`, same emitter | websecure; exact Host+`/version.json`, GET/HEAD only→web8080 | Temporary P1 staging resource; historical source is not proof it remains served. |
| `hosting` redirect stage, same emitter | web; exact Host, GET/HEAD non-API root→HTTPS redirect | No native/private API route; no IP restriction. |
| Android resources referenced by `https_prepare.py:validate` | `android-agent-download/{apk-download,connection-acme,connection-https,connection-signal,remote-http-redirect}`; `/android-agent`, ACME, connection/remote paths | References seal names/Host/path only. Actual entrypoints/annotations/middleware/backend configuration belongs to the other application and is not fully defined here. Must inspect in the production stage. |
| `devday-reports/reports`, same validation | `devday.<original-IP>.sslip.io`, `/` | Other-app Ingress reference, not an owned complete manifest. Middleware/entrypoints/forwarding/access restrictions remain unknown until actual inventory. |
| `codex-trial/{trial,trial-access-probe}`, `infra/codex-trial/render.py` | websecure; configured Host; `/codex-trial` UI/API or version-only probe | Candidate `trial-access` is RemoteAddr ipAllowList, public individual /32 or /128 only. Private/Pod CIDRs and header-based ipStrategy are refused by the renderer. Thus this source does not establish a cluster-only bypass. PARITY's last observation says no trial IngressRoute in production, and namespace absent in dev; fresh state still required before operation. |
| `issue97-r2-dev`, `ops.research_dev_fixture.bundle` | temporary websecure; synthetic exact Host; `/api` except internal→api8080 | Exact-name one-hour TLS and encoding middleware. The prior window removed its UID-owned objects. No private Host or IP ACL is emitted. |
| Docker file-provider fixtures: `https_fixture.py`, `codex-trial/prepare_proxy.py`, Compose route fixtures | translated root/trial rules, low-priority Android/ACME control routes and synthetic control services | Local test-only configurations, not Kubernetes served inventory. The trial loopback127/32 ACL in `prepare_proxy.py` is a Docker test override, not a production Pod allowance. |

Search covered every repository Ingress/IngressRoute/websecure/HostSNI and
ipAllowList/ipWhiteList/sourceRange definition. No repository-owned active
internal-only Host or public-blocked/Pod-allowed ACL was established. This is
**not certification that the actual shared controller has no such route**.
Other-app objects, any TCP/UDP route, Gateway API or file/internal provider and
entrypoint defaults must be included in the operator's all-provider inventory.

The reviewer supplied only a historical partial dev observation:
`artifacts/review/issue97/d7/historical-traefik-inputs.json`: Service
`kube-system/traefik`443→named `websecure`. Selector/actual Pod numeric port and
EndpointSlice mapping were not safely exported. **No443/8443 numeric backend
choice is made from that observation.** Fresh dev inputs close the dev gate;
production-specific inputs are finalized under its separately authorized plan.

Stop/report if an actual shared route, chained middleware, source-IP rule,
private Host, forwarded-header trust or upstream policy makes a service unavailable
from the internet but available from the newly admitted runner path. A public
URL or an application login by itself does not establish absence of that bypass.
Do not remove/change another app's restriction, broaden egress or silently add
target ingress. Unknown protection/provider coverage is unresolved, not a pass.
