"""Group-specific analysis choices and predictable request estimates."""
from roles import identity

DEFAULTS={'scoring':True,'include_applicants':True,'difficulty':4,
          'abyss':8,'nymrissa':0,'kithix':0,'detailed':False,'item_reference':320}


def normalize(values=None):
    result={**DEFAULTS,**(values or {})}
    for key in ('scoring','include_applicants','detailed'):result[key]=bool(result[key])
    for key,maximum in [('abyss',8),('nymrissa',1),('kithix',1)]:
        result[key]=min(maximum,max(0,int(result[key])))
    result['difficulty']=5 if result['difficulty']==5 else 4
    result['item_reference']=max(1,int(result['item_reference']))
    if not result['scoring']:result['detailed']=False
    return result


def group_identity(entries,region):
    return {'region':region.upper(),'members':sorted({identity(c['name'],c['realm']) for c in entries if not c.get('applicant')})}


def same_group(previous,current):
    if not previous or previous['region']!=current['region']:return False
    a,b=set(previous['members']),set(current['members'])
    return bool(a and b) and len(a&b)/max(len(a),len(b))>=.6


def target_bosses(zone,options):
    bosses=zone['encounters'];other={3379:'nymrissa',3513:'kithix'}
    abyss=[b for b in bosses if b['id'] not in other][:options['abyss']]
    return abyss+[b for b in bosses if b['id'] in other and options[other[b['id']]]]


def estimate(snapshot,options):
    """Incremental detailed work; existing successful evidence is reused."""
    from scoring_data import cached_histories, cached_higher, history_has_itemlevel
    from roles import identity
    options=normalize(options)
    if not options['scoring'] or not options['detailed']:
        return {'history_calls':0,'higher_calls':0,'calls':0,'points':0,'players':len(snapshot['characters'])}
    bosses=target_bosses(snapshot['zone'],options)
    histories=cached_histories(snapshot);higher=cached_higher(snapshot)
    history=sum(b['id'] not in histories.get(identity(c['name'],c['realm']),{}) or not history_has_itemlevel(snapshot,c,b['id'])
                for c in snapshot['characters'] for b in bosses)
    higher_players=sum(identity(c['name'],c['realm']) not in higher for c in snapshot['characters']) if options['difficulty']==4 else 0
    return {'history_calls':history,'higher_calls':(higher_players+4)//5,'calls':history+(higher_players+4)//5,
            'points':round(history*1.01+higher_players*5.01,1),'players':len(snapshot['characters'])}
