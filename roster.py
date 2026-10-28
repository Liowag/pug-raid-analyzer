"""Reconcile Raider.IO rosters without reloading unchanged character rankings."""
import json
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from rio_import import parse_rio_export
from roles import identity, rio_roles, infer_role


def reusable(entry, prior):
    if not isinstance(prior,dict) or prior.get('error'):
        return False
    raw=prior.get('raw')
    data=raw.get('characterData') if isinstance(raw,dict) else None
    character=data.get('character') if isinstance(data,dict) else None
    rankings=character.get('zoneRankings') if isinstance(character,dict) else None
    # An explicit successful empty rankings list means "no public logs".
    # Missing/partial response data or a failed request is never a cache hit.
    if not isinstance(rankings,dict) or not isinstance(rankings.get('rankings'),(list,dict)):
        return False
    role=infer_role(prior) if entry['role']=='Unbekannt' else entry['role']
    compatible=entry['role']=='Unbekannt' or (infer_role(prior)=='Heal')==(role=='Heal')
    return compatible and (role!='Heal' or rankings.get('metric','dps')=='hps')


def pending_entries(entries, region, zone, difficulty, previous):
    same=previous and (previous['region'],previous['zone']['id'],previous['difficulty'])==(region,zone['id'],difficulty)
    old={identity(c['name'],c['realm']):c for c in previous['characters']} if same else {}
    return [entry for entry in entries if not reusable(entry,old.get(identity(entry['name'],entry['realm'])))]


def roster_from_export(text, source='both'):
    players, region = parse_rio_export(text, source, allow_empty=True)
    data = json.loads(text.lstrip('\ufeff'))
    roles = rio_roles(text.lstrip('\ufeff'), source)
    members = {identity(*entry.split('-', 2)[1:]) for entry in data.get('group', [])}
    return [{'name': name, 'realm': realm, 'role': roles[identity(name, realm)],
             'applicant': identity(name, realm) not in members} for name, realm in players], region


def reconcile(entries, region, zone, difficulty, previous, fetch, timestamp, fetch_many=None):
    same = previous and (previous['region'], previous['zone']['id'], previous['difficulty']) == (region, zone['id'], difficulty)
    old = {identity(c['name'], c['realm']): c for c in previous['characters']} if same else {}
    def compatible(entry):
        prior = old.get(identity(entry['name'], entry['realm']))
        return reusable(entry,prior)
    pending = [entry for entry in entries if not compatible(entry)]
    # Propagate the session's diagnostics context into each worker.
    with ThreadPoolExecutor(max_workers=4) as pool:
        if fetch_many:
            packages=[pending[i:i+5] for i in range(0,len(pending),5)]
            futures=[pool.submit(copy_context().run,fetch_many,package) for package in packages]
            fetched={identity(c['name'],c['realm']):c for future in futures for c in future.result()}
        else:
            futures = [(entry, pool.submit(copy_context().run, fetch, entry)) for entry in pending]
            fetched = {identity(entry['name'],entry['realm']): future.result() for entry,future in futures}
    characters, reused, loaded = [], 0, 0
    for entry in entries:
        prior = old.get(identity(entry['name'], entry['realm']))
        role = entry['role']
        if compatible(entry):
            character = deepcopy(prior)
            reused += 1
        else:
            character = fetched[identity(entry['name'],entry['realm'])]
            character['ranking_captured_at'] = timestamp
            loaded += 1
        if role != 'Unbekannt':
            character['role'] = role
        character['applicant'] = entry.get('applicant', False)
        characters.append(character)
    result = {**(previous if same else {}), 'kind': previous.get('kind', 'warcraftlogs') if same else 'warcraftlogs',
              'captured_at': previous['captured_at'] if same else timestamp, 'updated_at': timestamp,
              'region': region, 'zone': zone, 'difficulty': difficulty, 'characters': characters}
    removed = len(set(old) - {identity(e['name'], e['realm']) for e in entries})
    return result, (loaded, reused, removed)
