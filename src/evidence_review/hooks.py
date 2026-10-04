"""Caller-owned durable continuation, with no automatic retry of unknown side effects."""
from dataclasses import dataclass
from types import MappingProxyType
from typing import Callable


def frozen(value):
    if isinstance(value,dict):return MappingProxyType({k:frozen(v) for k,v in value.items()})
    if isinstance(value,list):return tuple(frozen(v) for v in value)
    return value


@dataclass
class Hooks:
    on_submission: Callable | None = None
    on_revision: Callable | None = None
    reconcile: Callable | None = None
    next_bundle: Callable | None = None


def run_hook(store,task_id,revision,hooks=None,reconcile=False):
    hooks=hooks or Hooks()
    state=store.load_task(task_id)
    if state['hook']['status']=='succeeded':return state
    if state['hook'].get('invoked') and not reconcile:return state
    if reconcile and hooks.reconcile is None:
        return store.hook_status(task_id,revision,{'status':'pending','identifier':None,'reason':'caller reconciliation required','invoked':True})
    sub=store.export_submission(task_id,revision)
    callback=hooks.reconcile if reconcile else (hooks.on_revision if sub.amendment_reason else hooks.on_submission)
    store.hook_status(task_id,revision,{'status':'pending','identifier':None,'reason':'continuation in progress; crash requires reconciliation','invoked':True})
    try:
        status=callback(frozen(sub.model_dump(mode='json'))) if callback else {'status':'succeeded','identifier':f'local:{task_id}:{revision}','reason':'local submission only; no external hook configured'}
        if set(status)-{'status','identifier','reason'} or status.get('status') not in ('pending','succeeded','failed'):
            raise ValueError('invalid hook result')
        if status['status']=='succeeded' and not status.get('identifier'):raise ValueError('success needs durable identifier')
    except Exception as exc:
        # Human work is already durable. Record callback failure, never imply remote success.
        status={'status':'failed','identifier':None,'reason':f'{type(exc).__name__}: {exc}'}
    return store.hook_status(task_id,revision,{**status,'invoked':True})
