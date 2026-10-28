"""Background work contains no Streamlit calls or session state access."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextvars import copy_context
from copy import deepcopy
import threading
import time
from analyzer import fetch_characters, token_for, now
from roster import reconcile, pending_entries
from preferences import selected_roster
from analysis_options import target_bosses
from scoring_data import load_histories, load_higher
from roles import identity
from api_usage import current_usage

POOL=ThreadPoolExecutor(max_workers=4,thread_name_prefix='raid-analysis')


class Job:
    def __init__(self,call):
        self.lock=threading.Lock();self.message='Basisdaten werden geladen …';self.cancelled=threading.Event()
        self.started=time.monotonic();self.progress=0.;self.preview=None
        self.ledger=current_usage.get();self.start_calls=self.ledger.snapshot()['calls'] if self.ledger else 0
        self.future=POOL.submit(copy_context().run,call,self)

    def update(self,text,progress=None):
        with self.lock:
            self.message=text
            if progress is not None:self.progress=progress

    def publish(self,snapshot):
        with self.lock:self.preview=deepcopy(snapshot)

    def view(self):
        with self.lock:return self.message,self.progress,self.preview

    def status(self):
        with self.lock:return self.message


def run_base(job,entries,region,zone,difficulty,previous,credentials,token=None,offline=False):
    entries=deepcopy(entries);previous=deepcopy(previous)
    requested={identity(c['name'],c['realm']) for c in pending_entries(entries,region,zone,difficulty,previous)}
    if not token and not offline and pending_entries(entries,region,zone,difficulty,previous):
        if not all(credentials):raise ValueError('Bitte WCL-API-Zugangsdaten in den Einstellungen hinterlegen.')
        token=token_for(*credentials)
    def fetch(entry):return {**entry,'raw':{},'error':'Keine passenden Testdaten vorhanden.'}
    result,stats=reconcile(entries,region,zone,difficulty,previous,fetch,now(),
        fetch_many=None if offline else lambda batch:fetch_characters(token,batch,region,zone['id'],difficulty))
    job.publish(result)
    return {'snapshot':result,'stats':stats,'token':token,'errors':[],'prior_snapshot':previous,'requested':requested}


def run_configured(job,base,entries,options,credentials,offline=False):
    snapshot=base['snapshot'];region=snapshot['region'];zone=snapshot['zone']
    prior=base.get('prior_snapshot')
    if prior and (prior['region'],prior['zone']['id'],prior['difficulty'])==(region,zone['id'],snapshot['difficulty']):
        cache={identity(c['name'],c['realm']):c for c in prior['characters']}
        cache.update({identity(c['name'],c['realm']):c for c in snapshot['characters']})
        snapshot={**snapshot,'characters':list(cache.values())}
    selected=selected_roster(entries,options['include_applicants'])
    job.update('Gruppen- und Bewerberdaten abgleichen …')
    result=run_base(job,selected,region,zone,options['difficulty'],snapshot,credentials,base.get('token'),offline)
    requested=base.get('requested',set())|result.get('requested',set())
    final_ids={identity(c['name'],c['realm']) for c in selected}
    prior_ids={identity(c['name'],c['realm']) for c in (base.get('prior_snapshot') or {}).get('characters',[])}
    result['requested']=requested
    result['stats']=(len(final_ids&requested),len(final_ids-requested),len(prior_ids-final_ids))
    snapshot=result['snapshot'];token=result['token']
    job.publish(snapshot)
    if options['scoring'] and options['detailed'] and not offline:
        def auth():
            nonlocal token
            with auth_lock:
                if not token:token=token_for(*credentials)
                return token
        auth_lock=threading.Lock()
        bosses=target_bosses(zone,options)
        valid=[c for c in snapshot['characters'] if not c.get('error')]
        detail_snapshot={**snapshot,'characters':valid}
        progress_lock=threading.Lock()
        fractions={'Kill-Historien':0.,**({'Mythic':0.} if options['difficulty']==4 else {})}
        labels={name:'wartet' for name in fractions}
        def report(name,done,total):
            with progress_lock:
                fractions[name]=done/total if total else 1.
                labels[name]=f'{done}/{total}' if total else 'fertig'
                job.update(' · '.join(f'{key}: {labels[key]}' for key in labels),.1+.85*sum(fractions.values())/len(fractions))
        job.update('Kill-Historien und Zusatzdaten parallel laden …',.1)
        def histories():
            return load_histories(detail_snapshot,valid,bosses,auth,
                progress=lambda done,total:report('Kill-Historien',done,total),require_itemlevel=True,cancelled=job.cancelled.is_set)
        def higher():
            return load_higher(detail_snapshot,auth,progress=lambda done,total:report('Mythic',done,total),cancelled=job.cancelled.is_set)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures={pool.submit(copy_context().run,histories):'Kill-Historien'}
            if options['difficulty']==4:
                futures[pool.submit(copy_context().run,higher)]='Mythic'
            for future in as_completed(futures):
                name=futures[future]
                try:result['errors']+=future.result()
                except Exception as error:result['errors'].append(name+': '+str(error))
                report(name,1,1)
    job.update('Auswertung abgeschlossen',1.)
    result['token']=token
    return result
