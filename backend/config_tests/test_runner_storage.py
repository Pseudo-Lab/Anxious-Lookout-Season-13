"""Root Docker tmpfs tests: command planning only, no loop devices/mount syscalls."""
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
import os

import pytest
from ops import runner_storage as storage
from runner.lifecycle import Refused


def setup(monkeypatch):
    root = Path('/var/lib/anxious-lookout')/uuid4().hex
    root.mkdir(mode=0o700)
    original = Path.read_text
    monkeypatch.setattr(Path, 'read_text', lambda self, *a, **kw:
        f'1 2 0:1 / {root} rw shared:10 - tmpfs tmpfs rw\n' if str(self)=='/proc/self/mountinfo' else original(self,*a,**kw))
    monkeypatch.setattr(storage.shutil, 'disk_usage', lambda _:SimpleNamespace(free=20*storage.BYTES))
    monkeypatch.setattr(os, 'posix_fallocate', lambda fd, start, size:os.ftruncate(fd,size))
    monkeypatch.setattr(os.path, 'ismount', lambda _:True)
    return root/uuid4().hex


def test_new_allocation_has_two_bounded_images_and_never_reformats_existing(monkeypatch):
    target = setup(monkeypatch); calls=[]
    storage.allocate(target, run=lambda args,**kwargs:calls.append(args))
    assert [args[0] for args in calls] == ['mkfs.ext4','mount','mkfs.ext4','mount']
    assert all((target/(kind+'.ext4')).stat().st_size == storage.BYTES for kind in ('native','control'))
    assert all((target/kind/'data').is_dir() for kind in ('native','control'))
    with pytest.raises(FileExistsError): storage.allocate(target,run=lambda *a,**kw:pytest.fail('must not repeat'))
    assert len(calls)==4


def test_storage_reserve_refuses_before_any_mutation(monkeypatch):
    target = setup(monkeypatch)
    monkeypatch.setattr(storage.shutil,'disk_usage',lambda _:SimpleNamespace(free=11*storage.BYTES))
    with pytest.raises(Refused): storage.allocate(target,run=lambda *a,**kw:pytest.fail('must not run'))
    assert not target.exists()


def test_failed_format_keeps_permit_and_image(monkeypatch):
    target = setup(monkeypatch)
    def fail(*a,**kw): raise OSError('synthetic failure')
    with pytest.raises(OSError): storage.allocate(target,run=fail)
    assert target.exists() and (target/'native.ext4').exists()
    with pytest.raises(FileExistsError): storage.allocate(target,run=fail)
