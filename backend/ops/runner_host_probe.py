"""Bounded host TCP8081 attempt; socket outcome is NEVER a policy-deny verdict."""
import argparse
from datetime import datetime, timezone
import ipaddress
import json
from pathlib import Path
import socket

from ops.runner_one_shot import private_file, persist, require


def probe(health_file, output):
    health = json.loads(private_file(health_file))
    address = ipaddress.ip_address(health['podIP'])
    require(address.version == 4 and address in ipaddress.ip_network('10.42.0.0/16'))
    started = datetime.now(timezone.utc).isoformat()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(3)
        try:
            sock.connect((str(address), 8081))
            outcome = 'connected'
        except (TimeoutError, OSError):
            outcome = 'not-connected'
    persist(output, dict(case='host-other-port', podUid=health['podUid'], destination=str(address),
                         protocol='TCP', port=8081, started=started,
                         finished=datetime.now(timezone.utc).isoformat(), socketOutcome=outcome,
                         policyVerdict='unverified', requirement='correlate Cilium endpoint identity/revision/verdict'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--health', type=Path, default=Path('/evidence/health.json'))
    parser.add_argument('--output', type=Path, default=Path('/evidence/host-other-port.json'))
    args = parser.parse_args()
    try: probe(args.health, args.output)
    except Exception: parser.exit(1, 'Host probe refused; preserve evidence\n')


if __name__ == '__main__': main()
