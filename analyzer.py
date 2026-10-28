"""API access, local snapshots and table preparation (no Streamlit dependency)."""
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import requests
from api_diagnostics import measured, graph_label
from api_usage import current_usage, with_quota, RATE_ALIAS
from rankings import build_tables, extract_rankings, percentile_style, profile_url
from roles import infer_role
from session_storage import current_store, scoped_key, json_exists

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'data'
API = 'https://www.warcraftlogs.com/api/v2/client'
TOKEN = 'https://www.warcraftlogs.com/oauth/token'
DEFAULT_ZONE = 53
DIFFICULTIES = {'Normal': 3, 'Heroic': 4, 'Mythic': 5}
ZONE_QUERY = 'query {worldData { zones { id name frozen encounters { id name } difficulties { id name } } }}'
QUERY = '''query($name:String!,$server:String!,$region:String!,$zone:Int!,$difficulty:Int!){characterData{character(name:$name,serverSlug:$server,serverRegion:$region){name zoneRankings(zoneID:$zone,difficulty:$difficulty,metric:dps)}}}'''


def read_json(path):
    return Path(path).read_text(encoding='utf-8-sig')


def load_json(path):
    key=scoped_key(path)
    if key is not None:return current_store.get().read(key)
    return json.loads(read_json(path))


def save_json(path, data):
    key=scoped_key(path)
    if key is not None:
        current_store.get().write(key,data)
        return
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name+'.'+uuid4().hex+'.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def now():
    return datetime.now(timezone.utc).isoformat()


def load_credentials():
    try:
        text = (ROOT / 'wclogsclient.txt').read_text(encoding='utf-8-sig')
    except FileNotFoundError:
        text = ''
    values = {}
    for line in text.splitlines():
        match = re.match(r'^\s*(?:wcl_)?(client[ _]?secret|client[ _]?id|client|key|secret)(?:\s*[:=]\s*|\s+)(.+?)\s*$', line, re.I)
        if match:
            key = re.sub(r'[ _]', '', match[1].lower())
            values['secret' if key in ('clientsecret', 'secret', 'key') else 'id'] = match[2].strip()
    return (os.environ.get('WCL_CLIENT_ID') or values.get('id', ''),
            os.environ.get('WCL_CLIENT_SECRET') or values.get('secret', ''))


def token_for(cid, secret):
    def request():
        response = requests.post(TOKEN, data={'grant_type': 'client_credentials'}, auth=(cid, secret), timeout=15)
        response.raise_for_status()
        return response.json()['access_token']
    return measured('OAuth-Token', 'Warcraft Logs OAuth', request)


def request_graph(token, query, variables=None, partial=False, read_timeout=25):
    operation,target=graph_label(query,variables or {})
    def request():
        ledger=current_usage.get()
        metadata=None
        status='OK'
        try:
            response = requests.post(API, json={'query':with_quota(query), 'variables':variables or {}},
                                     headers={'Authorization': f'Bearer {token}'}, timeout=(10,read_timeout))
            response.raise_for_status()
            result=response.json()
            data=result.get('data') or {}
            metadata=data.pop(RATE_ALIAS,None)
            errors=result.get('errors') or []
            if errors:
                status='GraphQL-Teilfehler' if data else 'GraphQL-Fehler'
                if not partial:
                    raise RuntimeError('; '.join(error.get('message','API-Fehler') for error in errors))
            return (data,errors) if partial else data
        except Exception as error:
            response=getattr(error,'response',None)
            status=f'HTTP {response.status_code}' if isinstance(error,requests.HTTPError) and response is not None else type(error).__name__
            raise
        finally:
            if ledger is not None:
                ledger.record(metadata,operation,target,status)
    return measured(operation,target,request)


def fetch_characters(token, entries, region, zone, difficulty, batch_size=5):
    """Fetch one bounded package; retain successful aliases on partial errors."""
    output=[]
    for start in range(0,len(entries),batch_size):
        batch=entries[start:start+batch_size]
        definitions=['$region:String!','$zone:Int!','$difficulty:Int!']
        variables={'region':region,'zone':zone,'difficulty':difficulty}
        selections=[]
        for index,entry in enumerate(batch):
            definitions.extend([f'$name{index}:String!',f'$server{index}:String!'])
            variables[f'name{index}']=entry['name']
            variables[f'server{index}']=entry['realm'].strip().lower().replace(' ','-')
            metric='hps' if entry.get('role')=='Heal' else 'dps'
            selections.append(f'p{index}:character(name:$name{index},serverSlug:$server{index},serverRegion:$region)'
                '{name zoneRankings(zoneID:$zone,difficulty:$difficulty,metric:'+metric+')}')
        query='query RaidRoster('+','.join(definitions)+'){characterData{'+''.join(selections)+'}}'
        try:
            data,errors=request_graph(token,query,variables,partial=True,read_timeout=60)
        except (requests.RequestException,RuntimeError,ValueError,KeyError) as error:
            message='Zeitüberschreitung beim Abruf. Liste erneut einfügen oder „Alle Daten neu laden“ verwenden.' if isinstance(error,requests.Timeout) else str(error)
            data,errors={},[{'message':message}]
        for index,entry in enumerate(batch):
            alias=f'p{index}'
            info=(data.get('characterData') or {}).get(alias)
            relevant=[e for e in errors if not e.get('path') or alias in e['path'] or e['path']==['characterData']]
            character={**entry,'role':entry.get('role','Unbekannt'),'raw':{'characterData':{'character':info}},
                       'error':'; '.join(e.get('message','API-Fehler') for e in relevant) if relevant else '' if info else 'Charakter nicht gefunden'}
            character['role']=infer_role(character)
            output.append(character)
    # Unknown roles start as DPS; correct only newly identified healers, in
    # packages too. Already-known healers were requested as HPS immediately.
    healers=[(i,c) for i,(e,c) in enumerate(zip(entries,output)) if e.get('role')!='Heal' and c['role']=='Heal' and not c['error']]
    if healers:
        corrected=fetch_characters(token,[{**c,'role':'Heal'} for _,c in healers],region,zone,difficulty,batch_size)
        for (index,_),character in zip(healers,corrected):
            output[index]=character
    return output


def raid_zones(zones):
    return sorted((zone for zone in zones if any(d.get('id') in (3, 4, 5) for d in zone.get('difficulties', []))),
                  key=lambda zone: zone['id'], reverse=True)


def cached_zones():
    path = DATA / 'raid_zones.json'
    if json_exists(path):
        stored = load_json(path)
        return raid_zones(stored['zones']), stored.get('captured_at'), True
    return raid_zones(load_json(DATA / 'raid_zones_seed.json')), None, current_store.get() is not None


def refresh_zones(token):
    zones = request_graph(token, ZONE_QUERY)['worldData']['zones']
    save_json(DATA / 'raid_zones.json', {'captured_at': now(), 'zones': zones})
    return raid_zones(zones)


def parse_lines(text):
    players = []
    seen = set()
    for line in text.splitlines():
        line = re.sub(r'\s+\d{2,3}[.,]\d{1,2}\s*$', '', line.strip(' |\t'))
        if not line or line.startswith('#'):
            continue
        line = line.replace(' - ', '-', 1)
        parts = line.split('\t', 1) if '\t' in line else line.split('-', 1)
        if len(parts) != 2:
            continue
        name, realm = (part.strip() for part in parts)
        identity = (name.casefold(), realm.casefold())
        if name and realm and identity not in seen:
            seen.add(identity)
            players.append((name, realm))
    return players


def fetch_character(token, name, realm, region, zone, difficulty, role='Unbekannt'):
    result = {'name': name, 'realm': realm, 'role': role, 'error': '', 'raw': None}
    try:
        # Raider.IO supplies canonical slugs. Preserve Cyrillic and apostrophes;
        # WCL accepts the server slug as a GraphQL string variable.
        server = realm.strip().lower().replace(' ', '-')
        metric = 'hps' if role == 'Heal' else 'dps'
        data = request_graph(token, QUERY.replace('metric:dps', f'metric:{metric}'), {'name': name, 'server': server, 'region': region,
                                          'zone': zone, 'difficulty': difficulty})
        result['raw'] = data
        result['role'] = infer_role(result)
        # Imports without a group role query healing only when the
        # available specializations identify the player as a healer.
        if result['role'] == 'Heal' and metric != 'hps':
            result['raw'] = request_graph(token, QUERY.replace('metric:dps', 'metric:hps'),
                                         {'name': name, 'server': server, 'region': region,
                                          'zone': zone, 'difficulty': difficulty})
        if not (data.get('characterData') or {}).get('character'):
            result['error'] = 'Charakter nicht gefunden'
    except (requests.RequestException, RuntimeError, ValueError, KeyError) as error:
        result['error'] = str(error)
    return result


def load_test_snapshot(public=False):
    if public:return load_json(DATA/'public_demo.json')
    for path in (DATA/'sample_response.json',DATA/'last_analysis.json',DATA/'public_demo.json',
                 DATA/'demo_analysis.json',ROOT/'data'/'public_demo.json'):
        if path.exists():return load_json(path)
    raise FileNotFoundError('Keine Demodaten vorhanden')


def save_analysis(snapshot):
    if current_store.get() is not None:return # UI state already owns its snapshot.
    save_json(DATA / 'last_analysis.json', snapshot)
    sample = load_json(ROOT / 'tests/fixtures/rio_group.json')['group']
    expected = {tuple(entry.split('-', 2)[1:]) for entry in sample}
    actual = {(char['name'], char['realm']) for char in snapshot['characters']}
    if actual == expected and not any(char.get('error') for char in snapshot['characters']):
        save_json(DATA / 'sample_response.json', snapshot)


