"""Keep the current Raider.IO group role separate from historical rankings."""
import json
from collections import Counter

HEAL_SPECS = {'Restoration', 'Holy', 'Discipline', 'Mistweaver', 'Preservation'}
TANK_SPECS = {'Blood', 'Protection', 'Guardian', 'Brewmaster', 'Vengeance'}


def identity(name, realm):
    return f'{name.casefold()}|{realm.casefold()}'


def rio_roles(text, source='group'):
    data = json.loads(text)
    result = {}
    def add(entry):
        if isinstance(entry, list):
            for member in entry:
                add(member)
            return
        bits, name, realm = entry.split('-', 2)
        role = {'4': 'Tank', '2': 'Heal', '1': 'DD'}.get(bits, 'Unbekannt')
        result.setdefault(identity(name, realm), role)
    for key in (('group', 'queue') if source == 'both' else (source,)):
        for entry in data.get(key, []):
            add(entry)
    return result


def infer_role(character):
    if character.get('role') in ('Tank', 'Heal', 'DD'):
        return character['role']
    raw = character.get('raw') or {}
    char = (raw.get('characterData') or {}).get('character') or {}
    ranking = char.get('zoneRankings') or {}
    entries = ranking.get('rankings', []) if isinstance(ranking, dict) else []
    specs = [row.get('bestSpec') or row.get('spec') for row in entries if isinstance(row, dict)]
    votes = Counter('Heal' if spec in HEAL_SPECS else 'Tank' if spec in TANK_SPECS else 'DD'
                    for spec in specs if isinstance(spec, str) and spec)
    return votes.most_common(1)[0][0] if votes else 'Unbekannt'
