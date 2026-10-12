"""Fixed numeric trial critical-path admission; no retries or deadline growth."""
import argparse
import json
from pathlib import Path
import time

# Seconds, including client kill allowance for network commands. Preparations
# before the holder starts are not on this path. Each group has its own outer
# bound and preserves all raw responses; reads within a group are parallel.
STAGES = ('original', 'collector', 'binding', 'lease', 'create', 'realization', 'emit', 'dispatch')
BOUNDS_NS = dict(zip(STAGES, (3, 6, 2, 1, 2, 2, 1, 1)))
BOUNDS_NS = {k: v*1_000_000_000 for k, v in BOUNDS_NS.items()}


def admit(stage, start_ns, now_ns):
    if stage not in BOUNDS_NS or any(type(v) is not int for v in (start_ns, now_ns)) or not 0 < start_ns <= now_ns:
        raise ValueError()
    cutoff = start_ns+18_000_000_000
    if now_ns+BOUNDS_NS[stage] >= cutoff:
        raise ValueError()
    return min(cutoff-1, now_ns+BOUNDS_NS[stage])


def completed(start_ns, deadline_ns, now_ns):
    if (any(type(v) is not int for v in (start_ns, deadline_ns, now_ns))
            or not 0 < start_ns <= now_ns < deadline_ns < start_ns+18_000_000_000): raise ValueError()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', choices=STAGES, required=True)
    parser.add_argument('--holder', required=True)
    parser.add_argument('--completed', help='Saved stage admission JSON; never regenerate to extend it')
    args = parser.parse_args()
    try:
        receipt = json.loads(Path(args.holder).read_text())
        if (receipt['bootId'] != Path('/proc/sys/kernel/random/boot_id').read_text().strip()
                or receipt['timeNamespace'] != str(Path('/proc/self/ns/time').readlink())): raise ValueError()
        now = time.clock_gettime_ns(time.CLOCK_BOOTTIME)
        if args.completed:
            saved = json.loads(Path(args.completed).read_text())
            if saved['stage'] != args.stage: raise ValueError()
            completed(receipt['startNs'], saved['stageDeadlineNs'], now)
            print('{"stageCompletedWithinBound":true}')
        else:
            deadline = admit(args.stage, receipt['startNs'], now)
            print(json.dumps({'stage': args.stage, 'stageDeadlineNs': deadline,
                              'remainingMs': (deadline-now)//1_000_000}))
    except Exception:
        parser.exit(1, 'Numeric critical-path budget insufficient; cleanup without retry\n')
