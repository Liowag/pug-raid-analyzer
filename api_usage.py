"""Always-on quota accounting, scoped to the current application's session."""
from contextvars import ContextVar
from datetime import datetime, timezone
from threading import Lock
from time import time

current_usage = ContextVar('api_usage', default=None)
RATE_ALIAS = 'raidAnalyzerRate'
RATE_SELECTION = RATE_ALIAS + ':rateLimitData{limitPerHour pointsSpentThisHour pointsResetIn currentRequest}'


def with_quota(query):
    end=query.rfind('}')
    return query[:end]+'\n'+RATE_SELECTION+'\n'+query[end:]


def request_cost(metadata):
    """Only accept a server-supplied numeric cost, never infer a concurrent delta."""
    value=metadata.get('currentRequest')
    if isinstance(value,(int,float)) and not isinstance(value,bool):
        return value
    if isinstance(value,dict):
        for key in ('total','actualCost','actualPoints','points','cost','application'):
            candidate=value.get(key)
            if isinstance(candidate,(int,float)) and not isinstance(candidate,bool):
                return candidate
    return None


class UsageLedger:
    def __init__(self):
        self.lock=Lock()
        self.latest=None
        self.rows=[]
        self.calls=0
        self.known_cost=0
        self.unknown_calls=0

    def record(self, metadata, operation, target, status):
        stamp=time()
        row={'Zeit (UTC)':datetime.fromtimestamp(stamp,timezone.utc).isoformat(timespec='seconds'),
             'Abfrage':operation,'Ziel':target,'Status':status,'Punkte':request_cost(metadata or {})}
        with self.lock:
            self.calls+=1
            if row['Punkte'] is None:
                self.unknown_calls+=1
            else:
                self.known_cost+=row['Punkte']
            self.rows.append(row)
            self.rows=self.rows[-500:]
            if not isinstance(metadata,dict):
                return
            limit,spent,remaining=(metadata.get(key) for key in ('limitPerHour','pointsSpentThisHour','pointsResetIn'))
            if not all(isinstance(v,(int,float)) and not isinstance(v,bool) for v in (limit,spent,remaining)):
                return
            reset_at=stamp+remaining
            old=self.latest
            # Simultaneous responses may contain older counters. Never move
            # backwards within an hour; accept a lower count after a reset.
            if old and reset_at < old['reset_at']-10:
                return
            if old and abs(reset_at-old['reset_at'])<10:
                spent=max(spent,old['spent'])
            self.latest={'limit':limit,'spent':spent,'reset_at':reset_at,'observed_at':stamp}

    def snapshot(self):
        with self.lock:
            return {'latest':dict(self.latest) if self.latest else None,'calls':self.calls,
                    'known_cost':self.known_cost,'unknown_calls':self.unknown_calls,
                    'rows':[dict(row) for row in self.rows]}
