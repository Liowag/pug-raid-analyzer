from session_storage import json_exists
"""Optional scoring evidence. All reads are local unless explicitly requested."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextvars import copy_context
import time
from datetime import datetime
from threading import BoundedSemaphore
from analyzer import DATA, now, request_graph, fetch_characters, save_json, load_json
from boss_details import context, digest, get_top, ULATEK
from roles import identity, infer_role
from scoring import compact_history, number, weekly_window

HISTORY_VERSION=1
DETAIL_SLOTS=BoundedSemaphore(6)


def history_path(snapshot,character,boss):
    return DATA/'scoring'/'history'/(digest([HISTORY_VERSION,context(snapshot,character),boss])+'.json')


def cached_histories(snapshot):
    result={}
    for character in snapshot['characters']:
        values={}
        for boss in snapshot['zone']['encounters']:
            path=history_path(snapshot,character,boss['id'])
            try:
                if json_exists(path):values[boss['id']]=load_json(path)['samples']
                elif boss['id']==ULATEK:
                    top=get_top(snapshot,character,None,False)
                    if isinstance(top.get('history'),list):values[boss['id']]=top['history']
            except (OSError,ValueError,KeyError):pass
        result[identity(character['name'],character['realm'])]=values
    return result


def latest_gear(ranks):
    dated=[rank for rank in ranks if isinstance(rank,dict) and number(rank.get('startTime')) and rank['startTime']>0]
    if not dated:return None
    latest=max(dated,key=lambda r:r['startTime'])
    value=latest.get('itemLevel',latest.get('bracketData'))
    return {'startTime':latest['startTime'],'itemLevel':value if number(value) and value>0 else None}


def history_has_itemlevel(snapshot,character,boss):
    try:
        stored=load_json(history_path(snapshot,character,boss))
        window=weekly_window(snapshot['region'])
        fresh=not window or datetime.fromisoformat(stored['captured_at']).timestamp()>=window[0]
        return 'latest_gear' in stored and fresh
    except (OSError,ValueError,KeyError,TypeError):return False


def cached_latest_gear(snapshot,character,bosses):
    values=[]
    for boss in bosses:
        try:
            value=load_json(history_path(snapshot,character,boss['id'])).get('latest_gear')
            if isinstance(value,dict) and number(value.get('startTime')):values.append(value)
        except (OSError,ValueError,KeyError,TypeError):pass
    # Do not fall back to older gear if the newest log has no ilvl.
    return max(values,key=lambda r:r['startTime']) if values else None


def load_histories(snapshot,characters,bosses,token_provider,progress=None,refresh=False,require_itemlevel=False,cancelled=None):
    """Four independent history requests; shared ceiling of six detail requests."""
    if cancelled and cancelled():return []
    cached=cached_histories(snapshot)
    pending=[(c,b) for c in characters for b in bosses if refresh or b['id'] not in cached[identity(c['name'],c['realm'])]
             or (require_itemlevel and not history_has_itemlevel(snapshot,c,b['id']))]
    if not pending:return []
    token=token_provider() # UI auth state stays on the main thread.
    errors=[]
    def fetch(pair):
        if cancelled and cancelled():return
        character,boss=pair
        metric='hps' if infer_role(character)=='Heal' else 'dps'
        query='''query($name:String!,$realm:String!,$region:String!,$difficulty:Int!,$encounter:Int!){
        characterData{character(name:$name,serverSlug:$realm,serverRegion:$region){
        encounterRankings(encounterID:$encounter,difficulty:$difficulty,metric:METRIC,partition:-1,
        includeCombatantInfo:false,includeOtherPlayers:false,includeHistoricalGraph:false)}}}'''.replace('METRIC',metric)
        variables={'name':character['name'],'realm':character['realm'],'region':snapshot['region'],
                   'difficulty':snapshot['difficulty'],'encounter':boss['id']}
        with DETAIL_SLOTS:
            if cancelled and cancelled():return
            data=request_graph(token,query,variables,read_timeout=60)
        info=(data.get('characterData') or {}).get('character') or {}
        ranks=info.get('encounterRankings')
        if not isinstance(ranks,dict) or not isinstance(ranks.get('ranks'),list):
            raise ValueError('Unvollständige Kill-Historie')
        samples=compact_history(ranks,infer_role(character))
        save_json(history_path(snapshot,character,boss['id']),{'captured_at':now(),'samples':samples,'latest_gear':latest_gear(ranks['ranks'])})
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures={pool.submit(copy_context().run,fetch,pair):pair for pair in pending}
        for done,future in enumerate(as_completed(futures),1):
            character,boss=futures[future]
            try:future.result()
            except Exception as error:errors.append(f"{character['name']} · {boss['name']}: {error}")
            if progress:progress(done,len(pending))
    return errors


def main_path(snapshot,name,realm):
    return DATA/'scoring'/'mains'/(digest([snapshot['region'],snapshot['zone']['id'],snapshot['difficulty'],identity(name,realm)])+'.json')


def load_main(snapshot,name,realm,token_provider,refresh=False):
    return load_mains(snapshot,[{'name':name,'realm':realm}],token_provider,refresh)[0]


def load_mains(snapshot,references,token_provider,refresh=False):
    result={};pending=[]
    for ref in references:
        try:
            stored=load_json(main_path(snapshot,ref['name'],ref['realm']))
            if not refresh and time.time()-stored['fetched_at']<86400:
                result[identity(ref['name'],ref['realm'])]=stored['character'];continue
        except (OSError,ValueError,KeyError,TypeError):pass
        pending.append({**ref,'role':'Unbekannt'})
    if pending:
        characters=fetch_characters(token_provider(),pending,snapshot['region'],snapshot['zone']['id'],snapshot['difficulty'])
        errors=[]
        for character in characters:
            if character.get('error'):
                errors.append(character['name']+': '+character['error']);continue
            save_json(main_path(snapshot,character['name'],character['realm']),{'fetched_at':time.time(),'character':character})
            result[identity(character['name'],character['realm'])]=character
        if errors:raise ValueError('; '.join(errors))
    return [result[identity(ref['name'],ref['realm'])] for ref in references]


def higher_path(snapshot,character):
    return DATA/'scoring'/'higher'/(digest(context(snapshot,character))+'.json')


def cached_higher(snapshot):
    result={}
    for c in snapshot['characters']:
        try:
            stored=load_json(higher_path(snapshot,c))
            if time.time()-stored['fetched_at']<86400:
                result[identity(c['name'],c['realm'])]=stored['character']
        except (OSError,ValueError,KeyError,TypeError):pass
    return result


def load_higher(snapshot,token_provider,progress=None,cancelled=None):
    """Nearest higher difficulty; batches of five, only missing context entries."""
    if snapshot['difficulty'] not in (3,4):return []
    if cancelled and cancelled():return []
    cached=cached_higher(snapshot)
    pending=[c for c in snapshot['characters'] if identity(c['name'],c['realm']) not in cached]
    if not pending:return []
    token=token_provider()
    errors=[]
    packages=[pending[i:i+5] for i in range(0,len(pending),5)]
    def fetch(package):
        with DETAIL_SLOTS:
            if cancelled and cancelled():return []
            return fetch_characters(token,package,snapshot['region'],snapshot['zone']['id'],snapshot['difficulty']+1)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures={pool.submit(copy_context().run,fetch,package):package for package in packages}
        for done,future in enumerate(as_completed(futures),1):
            try:
                for c in future.result():
                    info=((c.get('raw') or {}).get('characterData') or {}).get('character') or {}
                    ranking=info.get('zoneRankings')
                    if c.get('error') or not isinstance(ranking,dict) or not isinstance(ranking.get('rankings'),(list,dict)):
                        errors.append(c['name']+': '+(c.get('error') or 'Unvollständige Rankings'));continue
                    save_json(higher_path(snapshot,c),{'fetched_at':time.time(),'character':c})
            except Exception as error:
                errors.append(', '.join(c['name'] for c in futures[future])+': '+str(error))
            if progress:progress(done,len(packages))
    return errors

