"""Prepare Warcraft Logs rankings for overview and character detail tables."""
import statistics
from urllib.parse import quote
from roles import infer_role


def profile_url(name, realm, region):
    return f'https://www.warcraftlogs.com/character/{region.lower()}/{quote(realm,safe="")}/{quote(name,safe="")}'


def profile_links(name,realm,region):
    return {'text':'','links':[
        {'url':profile_url(name,realm,region),'label':'Warcraft Logs','icon':'wcl'},
        {'url':f'https://raider.io/characters/{region.lower()}/{quote(realm,safe="")}/{quote(name,safe="")}','label':'Raider.IO','icon':'rio'}]}


def extract_rankings(character):
    raw = character.get('raw') or {}
    info = (raw.get('characterData') or {}).get('character') or {}
    data = info.get('zoneRankings') or {}
    if not isinstance(data, dict):
        return {}
    rankings = data.get('rankings') or []
    if isinstance(rankings, dict):
        rankings = rankings.values()
    output = {}
    for row in rankings:
        if not isinstance(row, dict):
            continue
        encounter = row.get('encounter') or {}
        encounter_id = row.get('encounterID') or encounter.get('id')
        if encounter_id is None:
            continue
        output[int(encounter_id)] = {
            'Best': row.get('rankPercent') if row.get('rankPercent') is not None else row.get('bestPercent'),
            'Median': row.get('medianPercent') if row.get('medianPercent') is not None else row.get('medianPerformanceAverage'),
            'Kills': row.get('totalKills'),
            'Höchster DPS': row.get('bestAmount'),
            'Spezialisierung': row.get('bestSpec') or row.get('spec'),
            'Schnellster Kill': duration(row.get('fastestKill')),
            'All-Star-Punkte': (row.get('allStars') or {}).get('points'),
        }
    return output


def mean(values):
    values = [value for value in values if isinstance(value, (int, float)) and not isinstance(value, bool)]
    return round(statistics.mean(values), 1) if values else None


def duration(milliseconds):
    if not isinstance(milliseconds, (int, float)) or milliseconds <= 0:
        return None
    seconds = int(milliseconds / 1000)
    return f'{seconds // 60}:{seconds % 60:02d}'


def build_tables(snapshot):
    rows, details = [], []
    for character in snapshot['characters']:
        role = infer_role(character)
        raw = character.get('raw') or {}
        char_info = (raw.get('characterData') or {}).get('character') or {}
        source_rankings = char_info.get('zoneRankings') or {}
        metric = source_rankings.get('metric', 'dps') if isinstance(source_rankings, dict) else 'dps'
        # Never display old damage fixtures as healer performance.
        wrong_metric = role == 'Heal' and metric != 'hps'
        ranks = extract_rankings(character)
        if wrong_metric:
            ranks = {}
        bosses = []
        for encounter in snapshot['zone'].get('encounters', []):
            values = ranks.get(encounter['id'], {})
            boss = {'Charakter': character['name'], 'Server': character['realm'], 'Boss': encounter['name'],
                    'Best': values.get('Best'), 'Median': values.get('Median'), 'Kills': values.get('Kills')}
            boss.update({key: values.get(key) for key in ('Höchster DPS', 'Spezialisierung', 'Schnellster Kill', 'All-Star-Punkte')})
            boss['Rolle'] = role
            boss['Metrik'] = 'HPS' if role == 'Heal' else 'DPS'
            boss['Höchster HPS'] = boss.pop('Höchster DPS') if role == 'Heal' else None
            details.append(boss)
            bosses.append(boss)
        raw = character.get('raw') or {}
        info = (raw.get('characterData') or {}).get('character') or {}
        averages = info.get('zoneRankings') or {}
        if not isinstance(averages, dict):
            averages = {}
        if wrong_metric:
            averages = {}
        best = averages.get('bestPerformanceAverage')
        median = averages.get('medianPerformanceAverage')
        best = round(best, 1) if isinstance(best, (int, float)) else mean(boss['Best'] for boss in bosses)
        median = round(median, 1) if isinstance(median, (int, float)) else mean(boss['Median'] for boss in bosses)
        rows.append({'Charakter': character['name'], 'Server': character['realm'],
                     'Rolle': role, 'Metrik': 'HPS' if role == 'Heal' else 'DPS',
                     'Best Ø': best, 'Median Ø': median,
                     'Bosse mit Logs': sum(boss['Best'] is not None for boss in bosses),
                     'Kills gesamt': sum(boss['Kills'] or 0 for boss in bosses),
                     'Status': character.get('error') or ('Heilungsrankings fehlen im gespeicherten Datensatz' if wrong_metric else 'OK' if best is not None else 'Keine öffentlichen Rankings'),
                     'Profil': profile_url(character['name'], character['realm'], snapshot['region'])})
    return rows, details


def percentile_style(value):
    if not isinstance(value, (int, float)):
        return ''
    color = '#e5cc80' if value >= 100 else '#e268a8' if value >= 99 else '#ff8000' if value >= 95 else '#a335ee' if value >= 75 else '#0070ff' if value >= 50 else '#1eff00' if value >= 25 else '#9d9d9d'
    return f'color: {color}; font-weight: 700'
