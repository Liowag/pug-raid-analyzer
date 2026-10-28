"""Explainable readiness heuristics, not calibrated clear probabilities."""
from datetime import datetime, timezone, timedelta
import math
import re
from statistics import mean
from roles import identity, infer_role, HEAL_SPECS, TANK_SPECS

VERSION='1.3'
SEPARATE_BOSSES={3379,3513}
WEIGHTS={'Tank':(.85,.12,.03,4),'Heal':(.70,.25,.05,8),'DD':(.55,.35,.10,20)}


def number(value):
    return isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value)


def kill_experience(kills):
    if not number(kills) or kills<=0:
        return 0.
    values=[0,28,53,72,86,95]
    if kills<=5:
        return float(values[int(kills)])
    return 95+5*(1-math.exp(-(kills-5)/4))


def boss_weights(bosses):
    n=len(bosses)
    return [1+2*(i/max(n-1,1))**1.5 for i in range(n)]


def rankings(character):
    raw=character.get('raw') or {}
    info=(raw.get('characterData') or {}).get('character') or {}
    data=info.get('zoneRankings') or {}
    if character.get('error') or not isinstance(data,dict):
        return {}
    entries=data.get('rankings') or []
    if isinstance(entries,dict):entries=entries.values()
    return {(r.get('encounter') or {}).get('id',r.get('encounterID')):r for r in entries if isinstance(r,dict)}


def role_for_spec(spec):
    if not isinstance(spec,str):return None
    return 'Heal' if spec in HEAL_SPECS else 'Tank' if spec in TANK_SPECS else 'DD' if spec else None


def compact_history(data,role):
    result=[]
    seen=set()
    for rank in data.get('ranks') or []:
        if not isinstance(rank,dict):continue
        if role_for_spec(rank.get('bestSpec') or rank.get('spec'))!=role:continue
        value=rank.get('historicalPercent',rank.get('rankPercent'))
        stamp=rank.get('startTime')
        report=rank.get('report') or {}
        key=(report.get('code'),report.get('fightID'),stamp)
        if not number(value) or not number(stamp) or stamp<=0 or not 0<=value<=100 or key in seen:continue
        seen.add(key)
        sample={'percent':value,'startTime':stamp}
        if re.fullmatch(r'[A-Za-z0-9]+',str(report.get('code',''))) and isinstance(report.get('fightID'),int) and not isinstance(report['fightID'],bool) and report['fightID']>0:
            sample['report']={'code':report['code'],'fightID':report['fightID']}
        result.append(sample)
    return sorted(result,key=lambda row:row['startTime'],reverse=True)[:50]


def recent_statistics(samples,reference_time):
    values=[]
    for sample in samples:
        value,stamp=sample.get('percent'),sample.get('startTime')
        if not number(value) or not number(stamp) or stamp<=0 or not 0<=value<=100:continue
        age=(reference_time-stamp/1000)/86400
        if age < -1:continue
        weight=2**(-max(age,0)/28)
        if weight>0:values.append((value,weight,stamp))
    if len(values)<3:return None
    total=sum(w for _,w,_ in values)
    average=sum(v*w for v,w,_ in values)/total
    low=sum(w for v,w,_ in values if v<25)/total
    spread=math.sqrt(sum(w*(v-average)**2 for v,w,_ in values)/total)
    ordered=sorted(values)
    cumulative=0
    median=ordered[-1][0]
    for v,w,_ in ordered:
        cumulative+=w
        if cumulative>=total/2:
            median=v;break
    chronological=sorted(values,key=lambda row:row[2])
    mid=len(chronological)//2
    trend=mean(v for v,_,_ in chronological[mid:])-mean(v for v,_,_ in chronological[:mid])
    return {'median':median,'low_rate':low,'spread':spread,'trend':trend,'samples':len(values)}


def weighted(values):
    items=[(value,weight) for value,weight in values if number(value)]
    return sum(value*weight for value,weight in items)/sum(weight for _,weight in items) if items else None


def logged_item_level(character,bosses):
    """Highest equipped ilvl evidenced by a selected boss's best log; not live gear."""
    values=[(r.get('bestRank') or {}).get('ilvl') for bid,r in rankings(character).items()
            if bid in {b['id'] for b in bosses}]
    return max((v for v in values if number(v) and v>0),default=None)


def weekly_window(region,reference_time=None):
    reference=datetime.fromtimestamp(reference_time,timezone.utc) if reference_time is not None else datetime.now(timezone.utc)
    # Retail resets are fixed UTC instants, independent of local DST.
    config={'EU':(2,4),'US':(1,15),'NA':(1,15)}.get(region.upper())
    if config is None:return None
    day,hour=config
    start=reference.replace(hour=hour,minute=0,second=0,microsecond=0)-timedelta(days=(reference.weekday()-day)%7)
    if start>reference:start-=timedelta(days=7)
    return start.timestamp(),(start+timedelta(days=7)).timestamp()


def current_week_kills(snapshot,histories,reference_time=None):
    window=weekly_window(snapshot['region'],reference_time)
    if window is None:return []
    start,end=window
    current=reference_time if reference_time is not None else datetime.now(timezone.utc).timestamp()
    rows=[]
    for c in snapshot['characters']:
        evidence=(histories or {}).get(identity(c['name'],c['realm']),{})
        for boss in snapshot['zone']['encounters']:
            samples=evidence.get(boss['id'],[])
            matches=[s for s in samples if number(s.get('startTime')) and start<=s['startTime']/1000<end and s['startTime']/1000<=current]
            if matches:
                row={'Charakter':c['name'],'Boss':boss['name']}
                for sample in sorted(matches,key=lambda s:s['startTime'],reverse=True):
                    report=sample.get('report') or {}
                    if re.fullmatch(r'[A-Za-z0-9]+',str(report.get('code',''))) and isinstance(report.get('fightID'),int) and not isinstance(report['fightID'],bool) and report['fightID']>0:
                        row['Log']=f"https://www.warcraftlogs.com/reports/{report['code']}#fight={report['fightID']}"
                        break
                rows.append(row)
    return rows


def score_player(character,bosses,histories=None,mains=None,reference_time=None,higher=None,item_reference=None):
    role=infer_role(character)
    ranks=rankings(character)
    histories=histories or {}
    reference_time=reference_time if reference_time is not None else datetime.now(timezone.utc).timestamp()
    experience=[];medians=[];bests=[];risks=[];details=[]
    evidence=0;history_count=0;trends=[]
    raw_info=((character.get('raw') or {}).get('characterData') or {}).get('character') or {}
    raw_rankings=raw_info.get('zoneRankings') or {}
    metric=raw_rankings.get('metric','dps') if isinstance(raw_rankings,dict) else None
    matching_metric=metric==('hps' if role=='Heal' else 'dps')
    for boss,weight in zip(bosses,boss_weights(bosses)):
        rank=ranks.get(boss['id'],{})
        kills=rank.get('totalKills')
        best=rank.get('rankPercent',rank.get('bestPercent'))
        median=rank.get('medianPercent')
        if not matching_metric:best=median=None
        if number(kills) or number(best) or number(median):evidence+=1
        exp=kill_experience(kills)
        experience.append((exp,weight))
        history=recent_statistics(histories.get(boss['id'],[]),reference_time)
        if history:
            history_count+=1
            median=history['median']
            # Low parses have several possible causes; no death inference.
            risk=.75*history['low_rate']+.25*min(1,history['spread']/40)
            trends.append(history['trend'])
            source=f"{history['samples']} Kills · zeitgewichtet"
        else:
            gap=max(0,best-median) if number(best) and number(median) else None
            risk=min(1,max(0,gap-15)/60) if gap is not None else 0
            source='Best–Median-Abstand (Näherung)' if gap is not None else 'keine Streuungsdaten'
        medians.append((median,weight));bests.append((best,weight));risks.append((risk,weight))
        details.append({'Boss':boss['name'],'Kills':kills if number(kills) else None,
            'Erfahrung':round(exp,1),'Gewicht':round(weight,2),'Median':median,'Best':best,
            'Streuung':source,'Niedrige Logs (%)':round(history['low_rate']*100,1) if history else None,
            'Trend':round(history['trend'],1) if history else None})
    exp=weighted(experience) or 0
    med=weighted(medians);best=weighted(bests)
    flags=[]
    if not evidence:flags.append('Keine verwertbaren öffentlichen Rankings')
    if role not in WEIGHTS:flags.append('Rolle unbekannt')
    if character.get('error'):flags.append(character['error'])
    if not matching_metric:flags.append('Leistungsmetrik passt nicht zur Rolle; nur Kill-Erfahrung und passende Historie verwendet')
    if history_count<len(bosses):flags.append('Streuung teilweise nur geschätzt; keine Todesauswertung')
    main_bonus=0;main_source=None
    for main in mains or []:
        if identity(main['name'],main['realm'])==identity(character['name'],character['realm']):continue
        main_ranks=rankings(main)
        if not main_ranks:continue
        main_exp=weighted([(kill_experience(main_ranks.get(b['id'],{}).get('totalKills')),w)
                           for b,w in zip(bosses,boss_weights(bosses))]) or 0
        bonus=min(10,max(0,main_exp-exp)*.35)
        if infer_role(main)!=role:bonus*=.65
        if bonus>main_bonus:
            main_bonus=bonus;main_source=f"{main['name']}-{main['realm']} (manuell zugeordnet)"
    higher_bonus=weighted([(min(8,kill_experience(rankings(higher or {}).get(b['id'],{}).get('totalKills'))*.08),w)
                           for b,w in zip(bosses,boss_weights(bosses))]) or 0
    ilvl=(character.get('scoring_gear') or {}).get('itemLevel')
    # Opt-in reference avoids hard-coding tier-specific gear ranges. Bonus only.
    item_bonus=min(5,max(0,(ilvl-item_reference)/3)) if number(ilvl) and number(item_reference) else 0
    penalty=0;score=None
    if evidence and role in WEIGHTS:
        ew,mw,bw,max_penalty=WEIGHTS[role]
        penalty=(weighted(risks) or 0)*max_penalty
        # Missing performance is unknown, not an invented zero parse.
        perf=[(med,mw),(best,bw)]
        if med is None or best is None:flags.append('Leistungswerte teilweise unbekannt; neutraler Ersatzwert 50')
        score=max(0,min(100,exp*ew+sum((value if value is not None else 50)*w for value,w in perf)-penalty+main_bonus+higher_bonus+item_bonus))
    return {'score':round(score,1) if score is not None else None,'role':role,'experience':round(exp,1),
            'median':round(med,1) if med is not None else None,'best':round(best,1) if best is not None else None,
            'penalty':round(penalty,1),'main_bonus':round(main_bonus,1),'main_source':main_source,
            'higher_bonus':round(higher_bonus,1),'item_level':ilvl,'item_bonus':round(item_bonus,1),
            'coverage':evidence/max(len(bosses),1),'history_bosses':history_count,'bosses':len(bosses),
            'trend':round(mean(trends),1) if trends else None,'flags':flags,'details':details}


def group_score(snapshot,bosses,histories=None,mains=None,reference_time=None,higher=None,item_reference=None):
    players={identity(c['name'],c['realm']):score_player(c,bosses,(histories or {}).get(identity(c['name'],c['realm'])),
        (mains or {}).get(identity(c['name'],c['realm'])),reference_time,
        (higher or {}).get(identity(c['name'],c['realm'])),item_reference) for c in snapshot['characters']}
    members=[(c,players[identity(c['name'],c['realm'])]) for c in snapshot['characters'] if not c.get('applicant')]
    groups={role:[p for c,p in members if p['role']==role] for role in WEIGHTS}
    flags=[]
    tanks=groups['Tank'];heals=groups['Heal'];dds=groups['DD']
    core_count=math.ceil(len(heals)/2)
    known_heals=sorted([p['score'] for p in heals if p['score'] is not None],reverse=True)
    ts=[p['score'] for p in tanks if p['score'] is not None]
    provisional=len(tanks)!=2 or len(ts)!=2 or not heals or len(known_heals)<core_count or not dds
    if len(ts)!=2:flags.append(f'Vorläufig: {len(ts)}/2 Tanks bewertbar; Tankbesetzung unvollständig')
    if not heals or len(known_heals)<core_count:flags.append('Vorläufig: Kernheilung noch nicht vollständig bewertbar')
    if not dds or not any(p['score'] is not None for p in dds):
        flags.append('Vorläufig: keine bewertbaren DDs');provisional=True
    unknown=sum(p['score'] is None for c,p in members)
    if unknown:
        flags.append(f'{unknown} Gruppenmitglieder ohne Score; Ergebnis unsicher');provisional=True
    components={};score=None
    if ts:components['Tanks']=.65*min(ts)+.35*mean(ts)
    if known_heals:components['Kernheiler']=mean(known_heals[:max(1,core_count)])
    ds=sorted(p['score'] for p in dds if p['score'] is not None)
    if ds:components['DDs']=.7*mean(ds)+.3*mean(ds[:max(1,math.ceil(len(ds)/4))])
    if known_heals:
        remaining=known_heals[core_count:]
        components['Übrige Heiler']=mean(remaining) if remaining else components['Kernheiler']
    weights={'Tanks':.45,'Kernheiler':.30,'DDs':.20,'Übrige Heiler':.05}
    effective={k:weights[k]/sum(weights[c] for c in components) for k in components}
    if components:
        score=sum(components[k]*effective[k] for k in components)
        if ts:score=min(score,min(ts)+20)
        if 'Kernheiler' in components:score=min(score,components['Kernheiler']+25)
        if ts and min(ts)<70:flags.append('Mindestens ein Tank liegt unter dem Richtwert 70')
        if sum(v>=70 for v in known_heals)<core_count:flags.append('Zu wenige Heiler erreichen den Richtwert 70')
    if provisional:flags.append('Vorläufiger Score nur aus vorhandenen Daten; fehlende Rollen sind keine bestätigte Raid-Tauglichkeit')
    coverage=mean(p['coverage'] for c,p in members) if members else 0
    if score is not None and len(bosses)>6:
        earlier=group_score(snapshot,bosses[:6],histories,mains,reference_time,higher,item_reference)
        if earlier['score'] is not None and score>earlier['score']:
            score=earlier['score']
            flags.append('Konservative Zielgrenze: vollständiger Raid wird nicht höher als 6 Bosse bewertet')
    return {'score':round(score,1) if score is not None else None,'players':players,'components':components,
            'core_healers':core_count,'strong_core_healers':sum(v>=70 for v in known_heals[:core_count]),
            'coverage':coverage,'members':len(members),'flags':flags,'version':VERSION,
            'provisional':provisional,'effective_weights':effective}
