"""Read the Group Search JSON copied from the Raider.IO addon."""

import json
import re


def parse_rio_export(text, source='group', allow_empty=False):
    """Return unique (name, realm slug) pairs and the export's region."""
    if source not in ('group', 'queue', 'both'):
        raise ValueError('Unbekannte Import-Auswahl.')
    try:
        data = json.loads(text.lstrip('\ufeff'))
    except (ValueError, TypeError):
        raise ValueError('Bitte den vollständigen JSON-Export aus Raider.IO einfügen.') from None
    if not isinstance(data, dict) or not {'group', 'queue'}.intersection(data):
        raise ValueError('Der Export enthält keine Gruppen- oder Bewerberliste.')
    region = data.get('region')
    if not isinstance(region, str) or region.upper() not in ('EU', 'US', 'KR', 'TW'):
        raise ValueError('Der Export enthält keine unterstützte Region (EU, US, KR, TW).')

    players = []
    seen = set()

    def add(entry):
        if not isinstance(entry, str):
            raise ValueError('Ungültiger Spielereintrag im Raider.IO-Export.')
        match = re.fullmatch(r'[0-7]-([^-\s]+)-([^\s]+)', entry)
        if not match:
            raise ValueError('Spielereintrag muss Rolle, Name und Server enthalten.')
        name, realm = match.groups()
        identity = (name.casefold(), realm.casefold())
        if identity not in seen:
            seen.add(identity)
            players.append((name, realm))

    keys = ('group', 'queue') if source == 'both' else (source,)
    for key in keys:
        entries = data.get(key, [])
        if not isinstance(entries, list):
            raise ValueError('Gruppen- und Bewerberdaten müssen Listen sein.')
        for entry in entries:
            # Multi-player applications are nested lists in Raider.IO's queue.
            if key == 'queue' and isinstance(entry, list):
                for member in entry:
                    add(member)
            else:
                add(entry)
    if not players and not allow_empty:
        raise ValueError('Die gewählte Liste enthält keine Spieler. Bitte die Import-Auswahl prüfen.')
    return players, region.upper()
