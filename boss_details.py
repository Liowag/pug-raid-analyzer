from session_storage import json_exists
"""On-demand boss metrics. One selected kill per character, cached report tables."""
import hashlib
import json
import re
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextvars import copy_context
from threading import Lock
import requests

from analyzer import DATA, now, request_graph, save_json, load_json
from roles import HEAL_SPECS, TANK_SPECS, identity, infer_role
from scoring import compact_history

ULATEK = 3492
FIELDS = {
    'heart': 'Heart-Schaden',
    'share': 'Heart-Anteil',
    'average': 'DD-Ø im Log',
    'delta': 'Differenz zum DD-Ø',
}
TOP_QUERY = '''query($name:String!,$realm:String!,$region:String!,$difficulty:Int!,$partition:Int){
 characterData{character(name:$name,serverSlug:$realm,serverRegion:$region){server{name slug}
 encounterRankings(encounterID:3492,difficulty:$difficulty,metric:METRIC,partition:$partition,
 includeCombatantInfo:false,includeOtherPlayers:false,includeHistoricalGraph:false)}}}'''
REPORT_QUERY = '''query($code:String!,$fight:Int!){reportData{report(code:$code){
 fights(fightIDs:[$fight]){id encounterID difficulty kill}
 playerDetails(fightIDs:[$fight],includeCombatantInfo:false,translate:true)
 heart:table(dataType:DamageDone,fightIDs:[$fight],filterExpression:"target.name = 'Venomous Heart'",translate:true)
 damage:table(dataType:DamageDone,fightIDs:[$fight],translate:true)
}}}'''


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:24]


def context(snapshot, character):
    return [character.get('ranking_captured_at', snapshot.get('captured_at')), snapshot.get('healing_captured_at'), snapshot['region'],
            snapshot['zone']['id'], snapshot['difficulty'], identity(character['name'], character['realm']), infer_role(character)]


def top_path(snapshot, character):
    return DATA / 'boss_details' / 'tops' / (digest(context(snapshot, character)) + '.json')


def choose_top(data, role):
    candidates = []
    for rank in data.get('ranks') or []:
        report = rank.get('report') or {}
        if not re.fullmatch(r'[A-Za-z0-9]+', str(report.get('code', ''))) or not isinstance(report.get('fightID'), int):
            continue
        spec = rank.get('bestSpec') or rank.get('spec')
        rank_role = 'Heal' if spec in HEAL_SPECS else 'Tank' if spec in TANK_SPECS else 'DD' if isinstance(spec,str) and spec else 'Unbekannt'
        if role != 'Unbekannt' and rank_role != role:
            continue
        if not isinstance(rank.get('rankPercent'), (int, float)):
            continue
        candidates.append(rank)
    return max(candidates, key=lambda rank: (rank['rankPercent'], rank.get('amount') or 0, rank.get('startTime') or 0), default=None)


def get_top(snapshot, character, token, allow_network):
    path = top_path(snapshot, character)
    if json_exists(path):
        try:
            cached=load_json(path)
            if isinstance(cached,dict) and 'rank' in cached and 'metric' in cached and (cached['rank'] is not None or cached.get('validated') or not allow_network):
                return cached
        except (OSError,ValueError):
            pass
    if not allow_network:
        raise ValueError('Top-Kampf noch nicht lokal gespeichert')
    raw = (character.get('raw') or {}).get('characterData', {}).get('character') or {}
    rankings = raw.get('zoneRankings') or {}
    metric = 'hps' if infer_role(character) == 'Heal' else 'dps'
    variables = {'name': character['name'], 'realm': character['realm'].lower().replace(' ', '-'),
                 'region': snapshot['region'], 'difficulty': snapshot['difficulty'],
                 'partition': rankings.get('partition') if isinstance(rankings, dict) else None}
    response = request_graph(token(), TOP_QUERY.replace('METRIC', metric), variables)
    info = (response.get('characterData') or {}).get('character')
    if not isinstance(info,dict):
        raise ValueError('Charakter für die Top-Kampf-Abfrage nicht verfügbar')
    data = info.get('encounterRankings')
    if not isinstance(data,dict) or not isinstance(data.get('ranks'),list):
        raise ValueError('Top-Kampf-Antwort unvollständig; beim nächsten Laden erneut versuchen')
    rank = choose_top(data, infer_role(character))
    result = {'captured_at': now(), 'metric': metric, 'rank': rank, 'server': info.get('server'),'validated':True,
              'history':compact_history(data,infer_role(character))}
    save_json(path, result)
    return result


def compact_report(report, fight, difficulty):
    fights = report.get('fights') or []
    if not any(row['id'] == fight and row['encounterID'] == ULATEK and row['kill'] and row['difficulty'] == difficulty for row in fights):
        raise ValueError('Der Report bestätigt keinen passenden Ula’tek-Kill')
    roster = ((report.get('playerDetails') or {}).get('data') or {}).get('playerDetails') or {}
    if not isinstance(roster.get('dps'), list) or not roster['dps']:
        raise ValueError('Keine verlässliche DD-Rollenliste im Kampf')
    heart = ((report.get('heart') or {}).get('data') or {}).get('entries')
    damage = ((report.get('damage') or {}).get('data') or {}).get('entries')
    if not isinstance(heart, list) or not heart:
        raise ValueError('Venomous Heart ist im Kampf nicht als Schadensziel aufgezeichnet')
    if not isinstance(damage, list):
        raise ValueError('Gesamtschaden im Kampf nicht verfügbar')
    players = {role: [{key: player.get(key) for key in ('id', 'name', 'server')} for player in roster.get(role, [])]
               for role in ('tanks', 'healers', 'dps')}
    # WCL's player row already includes pet contributions; never add pets again.
    amounts = lambda entries: {str(entry['id']): entry['total'] for entry in entries
                               if entry.get('type') != 'Pet' and isinstance(entry.get('total'), (int, float))}
    return {'captured_at': now(), 'players': players, 'heart': amounts(heart), 'damage': amounts(damage)}


def get_report(rank, difficulty, token, allow_network):
    code, fight = rank['report']['code'], rank['report']['fightID']
    path = DATA / 'boss_details' / 'reports' / f'{code}-{fight}-heart-v1.json'
    if json_exists(path):
        try:
            cached=load_json(path)
            if isinstance(cached,dict) and all(key in cached for key in ('players','heart','damage','captured_at')):
                return cached
        except (OSError,ValueError):
            pass
    if not allow_network:
        raise ValueError('Heart-Tabelle noch nicht lokal gespeichert')
    data = request_graph(token(), REPORT_QUERY, {'code': code, 'fight': fight})
    report = (data.get('reportData') or {}).get('report')
    if not report:
        raise ValueError('Top-Report nicht öffentlich verfügbar')
    result = compact_report(report, fight, difficulty)
    save_json(path, result)
    return result


def normalized_realm(value):
    return ''.join(char for char in (value or '').casefold() if char.isalnum())


class CharacterIdentityError(ValueError):
    pass


def compare_player(report, character, server=None):
    players = [player for group in report['players'].values() for player in group]
    realms={normalized_realm(character['realm'])}
    if server:
        realms.update(normalized_realm(server.get(key)) for key in ('name','slug'))
    realms.discard('')
    matches = [player for player in players if player['name'].casefold() == character['name'].casefold()
               and normalized_realm(player.get('server')) in realms]
    if len(matches) != 1:
        raise CharacterIdentityError('Charakter und Server konnten im Top-Kampf nicht eindeutig zugeordnet werden')
    player_id = str(matches[0]['id'])
    dps_ids = {str(player['id']) for player in report['players']['dps']}
    if not dps_ids:
        raise ValueError('Keine DDs für den Durchschnitt verfügbar')
    # Every participating DD counts, including zero-damage players. The selected
    # player also counts if they played DPS in this specific fight.
    average = sum(report['heart'].get(actor, 0) for actor in dps_ids) / len(dps_ids)
    amount = report['heart'].get(player_id, 0)
    total = report['damage'].get(player_id)
    return {'heart': amount, 'average': average, 'delta': amount-average,
            'relative_delta': (amount-average)/average if average > 0 else None,
            'share': amount/total*100 if isinstance(total,(int,float)) and total > 0 else None,
            'dd_count': len(dps_ids)}


def needs_network(snapshot,character):
    """Preflight on the UI thread, so workers never access Streamlit auth state."""
    try:
        top=get_top(snapshot,character,None,False)
        if not top['rank']:
            return not top.get('validated',False)
        report=get_report(top['rank'],snapshot['difficulty'],None,False)
        compare_player(report,character,top.get('server'))
        return False
    except Exception:
        return True


def collect(snapshot, token_provider=None, allow_network=False, progress=None, completed=None):
    results = {}
    access_token = None
    characters=snapshot['characters']
    if allow_network and any(needs_network(snapshot,c) for c in characters):
        try:
            access_token=token_provider()
        except Exception as error:
            # Cached players still work even when authentication is unavailable.
            authentication_error=error
        else:
            authentication_error=None
    else:
        authentication_error=None
    def token():
        if not allow_network:
            raise ValueError('API-Aufruf im Testmodus verhindert')
        if authentication_error:
            raise authentication_error
        if access_token is None:
            raise ValueError('Keine API-Zugangsdaten für fehlende Heart-Daten verfügbar')
        return access_token

    report_guard=Lock()
    report_locks={}
    report_outcomes={}
    def shared_report(rank):
        key=(rank['report']['code'],rank['report']['fightID'],snapshot['difficulty'])
        with report_guard:
            lock=report_locks.setdefault(key,Lock())
        with lock:
            if key not in report_outcomes:
                try:
                    report_outcomes[key]=get_report(rank,snapshot['difficulty'],token,allow_network)
                except Exception as error:
                    report_outcomes[key]=error
            outcome=report_outcomes[key]
            if isinstance(outcome,Exception):
                raise outcome
            return outcome

    def player(character):
        key = identity(character['name'], character['realm'])
        try:
            top = get_top(snapshot, character, token, allow_network)
            if not top['rank']:
                raise ValueError('Kein passender öffentlicher Ula’tek-Top-Kampf vorhanden')
            rank = top['rank']
            report = shared_report(rank)
            try:
                result = compare_player(report, character, top.get('server'))
            except CharacterIdentityError:
                if not allow_network or top.get('server'):
                    raise
                # Upgrade old cached top references with the API's verified
                # localized realm name; never match by character name alone.
                query='query($name:String!,$realm:String!,$region:String!){characterData{character(name:$name,serverSlug:$realm,serverRegion:$region){server{name slug}}}}'
                data=request_graph(token(),query,{'name':character['name'],'realm':character['realm'],'region':snapshot['region']})
                top['server']=data['characterData']['character']['server']
                save_json(top_path(snapshot,character),top)
                result=compare_player(report,character,top['server'])
            result.update({'status': 'OK', 'top_percent': rank['rankPercent'], 'top_metric': top['metric'],
                           'report': rank['report'], 'captured_at': report['captured_at'],
                           'url': f"https://www.warcraftlogs.com/reports/{rank['report']['code']}#fight={rank['report']['fightID']}&type=damage-done"})
        except Exception as error:
            if isinstance(error,requests.Timeout):
                message='Zeitüberschreitung beim Laden der Heart-Details. Erneut laden versucht nur noch fehlende Daten.'
            elif isinstance(error,requests.HTTPError) and error.response is not None:
                message=f'HTTP {error.response.status_code} beim Abruf der Heart-Details. Erneut laden versucht fehlende Daten.'
            else:
                message=str(error)
            result = {'status': message}
        return key,result

    # Keep individual top requests isolated: one slow healer must not cause
    # successful players in a shared GraphQL package to time out as well.
    # All Streamlit progress callbacks execute here, never in a worker thread.
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures=[pool.submit(copy_context().run,player,c) for c in characters]
        for done,future in enumerate(as_completed(futures),1):
            key,result=future.result()
            results[key]=result
            if completed:
                completed(key,result,done,len(characters))
            if progress:
                progress(done,len(characters))
    return {identity(c['name'],c['realm']):results[identity(c['name'],c['realm'])] for c in characters}


def display_damage(value, signed=False):
    if not isinstance(value, (int, float)):
        return '–'
    prefix = '+' if signed and value > 0 else '−' if value < 0 else '±' if signed else ''
    amount = abs(value)
    precision = 1 if amount >= 1_000_000 else 2
    text = f'{amount/1_000_000:.{precision}f}'.replace('.', ',') + ' Mio.'
    return prefix + text


def comparison_color(result):
    if result.get('status') != 'OK':
        return '#9d9d9d'
    relative = result.get('relative_delta')
    if relative is None:
        return '#0070ff' if result.get('delta', 0) > 0 else '#1eff00'
    return '#9d9d9d' if relative < -.05 else '#1eff00' if relative <= .05 else '#0070ff' if relative < .20 else '#a335ee' if relative < .50 else '#ff8000' if relative < 1 else '#e268a8'
