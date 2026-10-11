"""Explicit mutation: four ephemeral SCHED_CLS loads, never attach/pin/run.

Not a read-only preflight. Run only on the selected host after separate approval.
Backend tests inject the loader and never call the BPF syscall.
"""
import argparse
import ctypes
import hashlib
import json
import os
import platform
from pathlib import Path
import struct
import sys


class LoadAttr(ctypes.Structure):
    _fields_ = [('prog_type', ctypes.c_uint32), ('insn_cnt', ctypes.c_uint32),
                ('insns', ctypes.c_uint64), ('license', ctypes.c_uint64),
                ('log_level', ctypes.c_uint32), ('log_size', ctypes.c_uint32),
                ('log_buf', ctypes.c_uint64), ('kern_version', ctypes.c_uint32),
                ('prog_flags', ctypes.c_uint32), ('prog_name', ctypes.c_char * 16),
                ('prog_ifindex', ctypes.c_uint32), ('expected_attach_type', ctypes.c_uint32)]


def instructions(family, protocol):
    if family not in (4, 6) or protocol not in ('tcp', 'udp'):
        raise ValueError()
    # Linux UAPI bpf_insn; lookup tuple v4=12, v6=36, initialized stack=40.
    values = [(0xbf, 6, 1, 0, 0)]  # r6 = context
    values += [(0x7a, 10, 0, off, 0) for off in (-8, -16, -24, -32, -40)]
    values += [(0xbf, 2, 10, 0, 0), (0x07, 2, 0, 0, -40),
               (0xb7, 3, 0, 0, 12 if family == 4 else 36),
               (0xb7, 4, 0, 0, -1), (0xb7, 5, 0, 0, 0),
               (0x85, 0, 0, 0, 99 if protocol == 'tcp' else 85)]
    null_at = len(values)
    values += [(0x15, 0, 0, 0, 0), (0xbf, 7, 0, 0, 0)]
    skip = []
    if protocol == 'tcp':
        # bpf_sock.state offset72; same TIME_WAIT/LISTEN exclusion as proxy.h.
        values += [(0x61, 8, 7, 72, 0)]
        for state in (6, 10):
            skip.append(len(values)); values += [(0x15, 8, 0, 0, state)]
    values += [(0xbf, 1, 6, 0, 0), (0xbf, 2, 7, 0, 0),
               (0xb7, 3, 0, 0, 0), (0x85, 0, 0, 0, 124)]
    release_at = len(values)
    values += [(0xbf, 1, 7, 0, 0), (0x85, 0, 0, 0, 86)]
    end_at = len(values)
    values += [(0xb7, 0, 0, 0, 0), (0x95, 0, 0, 0, 0)]
    for at, dest in [(null_at, end_at)] + [(at, release_at) for at in skip]:
        code, dst, src, _, imm = values[at]
        values[at] = code, dst, src, dest-at-1, imm
    return b''.join(struct.pack('<BBhi', code, dst | (src << 4), off, imm)
                    for code, dst, src, off, imm in values)


def load_only(program):
    if sys.byteorder != 'little' or ctypes.sizeof(ctypes.c_void_p) != 8 or ctypes.sizeof(LoadAttr) != 72:
        raise ValueError()
    syscall_number = {'aarch64': 280, 'x86_64': 321}.get(platform.machine())
    if syscall_number is None:
        raise ValueError()
    code = ctypes.create_string_buffer(program)
    license_text = ctypes.create_string_buffer(b'GPL\0')
    log = ctypes.create_string_buffer(16384)
    attr = LoadAttr(prog_type=3, insn_cnt=len(program)//8, insns=ctypes.addressof(code),
                    license=ctypes.addressof(license_text), log_level=1, log_size=len(log),
                    log_buf=ctypes.addressof(log), prog_name=b'access_tproxy')
    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    fd = libc.syscall(ctypes.c_long(syscall_number), ctypes.c_int(5), ctypes.byref(attr), ctypes.c_uint(72))
    if fd < 0:
        # Permission, unsupported helper, verifier disagreement: all stop. No retry.
        raise ValueError()
    os.close(fd)


def run(binding, machine, boot, kernel, loader):
    if (binding['machineSha256'] != hashlib.sha256(machine.strip().encode()).hexdigest()
            or binding['bootId'] != boot.strip() or binding['kernel'] != kernel):
        raise ValueError()
    hashes = []
    for family in (4, 6):
        for protocol in ('tcp', 'udp'):
            code = instructions(family, protocol)
            loader(code)
            hashes.append(hashlib.sha256(code).hexdigest())
    return {'nodeUid': binding['nodeUid'], 'bootId': binding['bootId'], 'kernel': kernel,
            'machineSha256': binding['machineSha256'], 'programType': 'SCHED_CLS',
            'programHashes': hashes, 'loadsClosed': 4, 'attached': False, 'pinned': False,
            'programExecuted': False, 'helperContextSupported': True,
            'fullCiliumDatapathSupported': 'unknown'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binding', required=True)
    parser.add_argument('--approved-load-only', action='store_true')
    args = parser.parse_args()
    try:
        caps = next(line.split(':', 1)[1].strip() for line in Path('/proc/self/status').read_text().splitlines()
                    if line.startswith('CapEff:'))
        if not args.approved_load_only or os.geteuid() != 0 or int(caps, 16) != ((1 << 39) | (1 << 12)):
            raise ValueError()
        binding = json.loads(Path(args.binding).read_text())
        print(json.dumps(run(binding, Path('/target-machine-id').read_text(),
                             Path('/target-boot-id').read_text(), platform.release(), load_only)))
    except Exception:
        parser.exit(1, 'Node kernel helper capability unknown; no NodeConfig/reload permitted\n')
