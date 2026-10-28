"""Boss focus, shared character rows and configurable overview columns."""
import pandas as pd
import streamlit as st

from boss_details import ULATEK, FIELDS, collect, comparison_color, context, digest, display_damage
from roles import identity
from rankings import percentile_style, profile_links, extract_rankings

ROLE_LABELS = [('Tank','Tanks · Damage'),('DD','Damage Dealer · Damage'),('Heal','Heiler · Healing'),('Unbekannt','Rolle noch nicht ermittelt')]


def player_groups(rows, snapshot):
    applicants = {identity(c['name'], c['realm']) for c in snapshot['characters'] if c.get('applicant')}
    for role,label in [('Bewerber', 'Bewerber · nach Best Ø')] + ROLE_LABELS:
        indices=[i for i,row in enumerate(rows) if (identity(row['Charakter'],row['Server']) in applicants if role=='Bewerber' else row['Rolle']==role and identity(row['Charakter'],row['Server']) not in applicants)]
        if role=='Bewerber':
            indices.sort(key=lambda i: rows[i]['Best Ø'] if isinstance(rows[i].get('Best Ø'),(int,float)) else -1, reverse=True)
        if indices:
            yield label, indices


def select_boss(encounter):
    st.session_state['view']='Bossfokus' if encounter is not None else 'Raidübersicht'
    st.session_state['_boss_id']=encounter
    st.session_state['_scroll_top']=True


def pin_column(field):
    selected=list(st.session_state.get('boss_columns',[]))
    if field in selected:
        selected.remove(field)
    else:
        selected.append(field)
    st.session_state['boss_columns']=selected


def navigation(snapshot):
    from ui_components import boss_navigation
    st.caption('RAID-NAVIGATION')
    boss_navigation(snapshot, select_boss)


def stored_results(snapshot):
    key=digest([context(snapshot,character) for character in snapshot['characters']])
    if st.session_state.get('heart_context')!=key:
        st.session_state['heart_context']=key
        st.session_state['heart_results']=collect(snapshot,allow_network=False) if any(e['id']==ULATEK for e in snapshot['zone']['encounters']) else {}
    return st.session_state.get('heart_results',{})


def metric_value(result, field):
    if result.get('status')!='OK':
        return '–'
    if field=='share':
        return f"{result['share']:.1f} %".replace('.',',') if result.get('share') is not None else '–'
    return display_damage(result.get(field),signed=field=='delta')


def boss_kill_tooltip(character,zone):
    ranks=extract_rankings(character)
    return '\n'.join(f"{boss['name']}: {ranks.get(boss['id'],{}).get('Kills') if isinstance(ranks.get(boss['id'],{}).get('Kills'),(int,float)) else 'unbekannt'}" for boss in zone['encounters'])


def player_rows(rows, snapshot, open_character, fields=None, boss=None, boss_details=None):
    from ui_components import render_table
    fields=fields or []
    results=stored_results(snapshot) if fields else {}
    lookup={(row['Charakter'],row['Server']):row for row in boss_details or []}
    for label,indices in player_groups(rows,snapshot):
        with st.expander(label,expanded=True):
            headers=['Charakter',{'label':'Links','sortable':False},'Best %' if boss else 'Best Ø','Median %' if boss else 'Median Ø']+(['Bosse'] if boss is None else [])
            headers += [{'label':('Ula’tek · ' if boss is None else '')+FIELDS[field],
                         'pin':field if boss==ULATEK else None,'active':field in st.session_state.get('boss_columns',[])} for field in fields]
            if boss==ULATEK:
                headers.append({'label':'Log','sortable':False})
            table_rows=[]
            for index in indices:
                row=rows[index]
                entry=lookup.get((row['Charakter'],row['Server']),{})
                result=results.get(identity(row['Charakter'],row['Server']),{})
                cells=[{'text':row['Charakter'],'character':index,'title':f"{row['Rolle']} · {row['Metrik']}"}]
                cells.append(profile_links(row['Charakter'],row['Server'],snapshot['region']))
                for field in ['Best','Median'] if boss else ['Best Ø','Median Ø']:
                    value=entry.get(field) if boss else row.get(field)
                    if boss==ULATEK and field=='Best' and result.get('status')=='OK':
                        value=result.get('top_percent',value)
                    cells.append({'text':f'{value:.1f}' if isinstance(value,(int,float)) else '–',
                                  'sort':value,
                                  'color':percentile_style(value).split(';')[0].removeprefix('color: ')})
                if boss is None:
                    cells.append({'text':f"{row['Bosse mit Logs']} / {len(snapshot['zone']['encounters'])}",'sort':row['Bosse mit Logs'],'title':boss_kill_tooltip(snapshot['characters'][index],snapshot['zone'])})
                for field in fields:
                    cells.append({'text':metric_value(result,field),'sort':result.get(field) if result.get('status')=='OK' else None,'color':comparison_color(result) if field=='delta' else '#d4b9ed'})
                if boss==ULATEK:
                    cells.append({'text':'↗' if result.get('url') else '–','url':result.get('url')})
                table_rows.append(cells)
            def action(event):
                if event['kind']=='character':
                    open_character(event['index'])
                elif event['kind']=='pin' and event['field'] in FIELDS:
                    pin_column(event['field'])
            group='applicants' if label.startswith('Bewerber') else rows[indices[0]]['Rolle']
            render_table(headers,table_rows,key=f'players_{boss or "overview"}_{group}',on_action=action)


def focus(snapshot, rows, details, open_character, test_mode, token_provider):
    boss=st.session_state.get('_boss_id')
    encounter=next((e for e in snapshot['zone']['encounters'] if e['id']==boss),snapshot['zone']['encounters'][0])
    st.subheader(encounter['name'])
    boss_rows=[row for row in details if row['Boss']==encounter['name']]
    if boss!=ULATEK:
        st.caption('Boss-Rankings deiner Gruppe. Zusatzanalysen starten bei Ula’tek.')
        player_rows(rows,snapshot,open_character,boss=boss,boss_details=boss_rows)
        return
    st.markdown('**Venomous Heart**')
    st.caption('Mit + übernimmst du die jeweilige Zusatzspalte in die Gesamtübersicht. Ein ✓ entfernt sie wieder.')
    fields=list(FIELDS)
    stored_results(snapshot)
    if st.button('Gespeicherte Heart-Daten neu laden' if test_mode else 'Heart-Details laden',key='load_heart'):
        progress=st.progress(0,text='Heart-Details vorbereiten …')
        status=st.empty()
        def finished(key,result,done,total):
            name=next((c['name'] for c in snapshot['characters'] if identity(c['name'],c['realm'])==key),key)
            status.caption(f"{done}/{total} fertig · {name}: {result['status']}")
        results=collect(snapshot,token_provider,allow_network=not test_mode,
                        progress=lambda done,total: progress.progress(done/total,text=f'Heart-Details: {done}/{total} Spieler fertig'),completed=finished)
        st.session_state['heart_results']=results
        progress.empty()
        status.empty()
        errors=sum(result.get('status')!='OK' for result in results.values())
        st.info(f"{len(results)-errors} Heart-Auswertungen verfügbar · {errors} nicht verfügbar. Erneut laden verwendet vorhandene Daten und versucht fehlende Abrufe erneut.")
    st.caption('Bis zu drei Spieler gleichzeitig. Gespeicherte Top-Kämpfe und Schadenstabellen werden wiederverwendet; erneutes Laden versucht fehlgeschlagene Abrufe erneut.')
    st.caption('Je Spieler nur der beste Ula’tek-Kill: DPS bei Tanks/DD, HPS bei Heilern. DD-Ø = alle DDs dieses Kampfes, einschließlich DDs mit 0 Heart-Schaden; ein DD selbst zählt mit. Haustierschaden bleibt beim Besitzer.')
    player_rows(rows,snapshot,open_character,fields=fields,boss=ULATEK,boss_details=boss_rows)
    st.caption('Differenzfarben nach relativem Abstand zum DD-Ø: grau unter −5 % · grün −5 bis +5 % · blau >5 % · violett ≥20 % · orange ≥50 % · pink ≥100 %. Keine Perzentile.')
    results=stored_results(snapshot)
    missing=[(character['name'],results.get(identity(character['name'],character['realm']),{}).get('status','Noch nicht geladen')) for character in snapshot['characters']
             if results.get(identity(character['name'],character['realm']),{}).get('status')!='OK']
    if missing:
        with st.expander(f'Hinweise: {len(missing)} nicht verfügbare Heart-Auswertungen'):
            for name,reason in missing:
                st.write(f'{name}: {reason}')
    export=[]
    for character in snapshot['characters']:
        result=results.get(identity(character['name'],character['realm']),{})
        export.append({'Charakter':character['name'],**{FIELDS[field]:result.get(field) for field in fields},
                       'DDs im Top-Kampf':result.get('dd_count'),'Top-Log':result.get('url'),'Hinweis':result.get('status')})
    st.download_button('Heart-Auswertung CSV',pd.DataFrame(export).to_csv(index=False).encode('utf-8-sig'),'ulatek_heart.csv','text/csv')
