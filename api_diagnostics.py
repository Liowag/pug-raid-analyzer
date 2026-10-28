"""Per-session, bounded API timings without credentials or request bodies."""
from contextvars import ContextVar
from datetime import datetime, timezone
from threading import Lock
from time import perf_counter

current_log = ContextVar('api_diagnostics', default=None)


class CallLog:
    def __init__(self):
        self.rows = []
        self.lock = Lock()

    def add(self, row):
        with self.lock:
            self.rows.append(row)
            self.rows = self.rows[-500:]

    def snapshot(self):
        with self.lock:
            return sorted((dict(row) for row in self.rows), key=lambda row: row['Start'])


def measured(operation, target, call):
    log = current_log.get()
    if log is None:
        return call()
    start = perf_counter()
    row = {'Start': datetime.now(timezone.utc).isoformat(timespec='milliseconds'),
           'Aufruf': operation, 'Ziel': target, 'Status': 'OK'}
    try:
        return call()
    except Exception as error:
        response=getattr(error,'response',None)
        status=getattr(response,'status_code',None)
        row['Status'] = f'HTTP {status}' if isinstance(status,int) else type(error).__name__
        raise
    finally:
        row['Dauer (s)'] = round(perf_counter() - start, 3)
        log.add(row)


def graph_label(query, variables):
    names=sorted((key for key in variables if key.startswith('name') and key[4:].isdigit()),key=lambda key:int(key[4:]))
    if names:
        return (f'Charakter-Sammelabfrage · {len(names)} Spieler',
                ', '.join(f"{variables[key]}-{variables.get('server'+key[4:],'')}" for key in names)+f" · {variables.get('region','')} · Zone {variables.get('zone','–')} · Schwierigkeit {variables.get('difficulty','–')}")
    if 'encounterRankings' in query:
        operation = 'Top-Kill · ' + ('HPS' if 'metric:hps' in query else 'DPS')
    elif 'zoneRankings' in query:
        operation = 'Charakter-Rankings · ' + ('HPS' if 'metric:hps' in query else 'DPS')
    elif 'reportData' in query:
        operation = 'Heart-/Schadenstabellen + Kampfrollen'
    else:
        operation = 'Raidzonen-Katalog'
    if variables.get('name'):
        target = f"{variables['name']}-{variables.get('server',variables.get('realm',''))} · {variables.get('region','')} · Zone {variables.get('zone','–')} · Schwierigkeit {variables.get('difficulty','–')}"
    elif variables.get('code'):
        target = f"Report {variables['code']} · Kampf {variables.get('fight','–')}"
    else:
        target = 'Warcraft Logs'
    return operation, target
