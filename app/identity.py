"""Cached build/model identity for release checks; no checkpoint bytes leave the host."""
from functools import lru_cache
import hashlib
import os
from pathlib import Path
import subprocess


@lru_cache(maxsize=4)
def _hash(path,mtime,size):
    h=hashlib.sha256()
    with open(path,'rb') as src:
        while block:=src.read(1024*1024):h.update(block)
    return h.hexdigest()


def file_hash(path):
    p=Path(path)
    if not p.is_file():return None
    stat=p.stat()
    return _hash(str(p),stat.st_mtime_ns,stat.st_size)


def build_identity(root):
    commit=os.environ.get('DEPTHWIZARD_BUILD_SHA')
    source_dirty=None
    if not commit:
        try:
            commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,timeout=2,stderr=subprocess.DEVNULL).decode().strip()
            source_dirty=bool(subprocess.check_output(['git','status','--porcelain','--','app','depthwizard','web'],
                cwd=root,timeout=2,stderr=subprocess.DEVNULL).strip())
        except (OSError,subprocess.SubprocessError):commit='unavailable'
    return {'commit':commit,'ui_sha256':file_hash(Path(root)/'web/app.js'),'source_dirty':source_dirty}
