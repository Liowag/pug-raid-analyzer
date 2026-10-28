"""Bounded, isolated in-memory caches propagated into background workers."""
from contextvars import ContextVar
from collections import OrderedDict
from pathlib import Path
from threading import RLock
import json

current_store=ContextVar('raid_session_store',default=None)
DATA_ROOT=Path(__file__).resolve().parent/'data'
PUBLIC_FILES={'raid_zones_seed.json','public_demo.json'}


class SessionStore:
    def __init__(self,max_bytes=16*1024*1024,max_entries=500):
        self.lock=RLock();self.items=OrderedDict();self.size=0
        self.max_bytes=max_bytes;self.max_entries=max_entries

    def has(self,key):
        with self.lock:return key in self.items

    def read(self,key):
        with self.lock:
            if key not in self.items:raise FileNotFoundError(key)
            self.items.move_to_end(key)
            return json.loads(self.items[key])

    def write(self,key,value):
        encoded=json.dumps(value,ensure_ascii=False).encode('utf-8')
        with self.lock:
            old=self.items.pop(key,None)
            if old:self.size-=len(old)
            if len(encoded)>self.max_bytes:return
            self.items[key]=encoded;self.size+=len(encoded)
            while self.size>self.max_bytes or len(self.items)>self.max_entries:
                _,old=self.items.popitem(last=False);self.size-=len(old)


def scoped_key(path):
    if current_store.get() is None:return None
    try:key=Path(path).resolve().relative_to(DATA_ROOT).as_posix()
    except ValueError:return None
    return None if key in PUBLIC_FILES else key


def json_exists(path):
    key=scoped_key(path)
    return current_store.get().has(key) if key is not None else Path(path).exists()
