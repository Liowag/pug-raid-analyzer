"""Readiness UI with a zero-extra-request default and explicit enrichments."""
import json
from copy import deepcopy
from analysis_options import normalize, target_bosses
import time
from urllib.parse import urlparse, unquote
import pandas as pd
import streamlit as st
from analyzer import DATA, save_json, load_json
from roles import identity
from scoring import group_score, SEPARATE_BOSSES, VERSION, current_week_kills, weekly_window
from scoring_data import cached_histories, load_histories, load_mains, main_path, cached_higher, load_higher, cached_latest_gear
from ui_components import frame_table

ASSIGNMENTS=DATA/'scoring'/'main_assignments.json'


def assignments():
    try:
        data=load_json(ASSIGNMENTS)
        return data if isinstance(data,dict) else {}
    except (OSError,ValueError):return {}


def assignment_key(snapshot,character):
    return snapshot['region']+':'+identity(character['name'],character['realm'])


def assigned_mains(snapshot):
    refs=assignments();result={}
    for character in snapshot['characters']:
        values=[]
        for ref in refs.get(assignment_key(snapshot,character),[]):
            try:
                cached=load_json(main_path(snapshot,ref['name'],ref['realm']))
                if time.time()-cached['fetched_at']<86400:values.append(cached['character'])
            except (OSError,ValueError,KeyError):pass
        result[identity(character['name'],character['realm'])]=values
    return result


def parse_reference(text,region):
    text=text.strip()
    if text.startswith('https://'):
        url=urlparse(text)
        parts=[unquote(p) for p in url.path.split('/') if p]
        if url.hostname not in ('raider.io','www.warcraftlogs.com','warcraftlogs.com') or len(parts)<4 or parts[0] not in ('characters','character') or parts[1].lower()!=region.lower():
            raise ValueError('Bitte ein Warcraft-Logs-/Raider.IO-Profil aus derselben Region eingeben.')
        return {'name':parts[3],'realm':parts[2]}
    parts=text.split('-',1)
    if len(parts)!=2 or not all(part.strip() for part in parts):
        raise ValueError('Bitte Name-Server oder einen Profil-Link eingeben.')
    return {'name':parts[0].strip(),'realm':parts[1].strip().lower().replace(' ','-')}


def label(value):
    if value is None:return 'Nicht bewertbar'
    return 'Gute Voraussetzungen' if value>=80 else 'Brauchbare Voraussetzungen' if value>=65 else 'Unsicher' if value>=45 else 'Kritische Voraussetzungen'


def render_scoring(snapshot,token_provider,test_mode,open_character):
    if snapshot['zone']['id']!=53:return
    st.subheader('Gruppen-Readiness · experimentell')
    st.caption('Heuristischer Score von 0–100, keine Erfolgswahrscheinlichkeit. Der Grundscore nutzt vorhandene Rankings ohne weitere API-Aufrufe. Zeitbudget, Kommunikation, Mechanikfehler und Raid-Aufstellung können das Ergebnis stark verändern.')
    bosses=snapshot['zone']['encounters']
    abyss=[boss for boss in bosses if boss['id'] not in SEPARATE_BOSSES]
    profiles={'abyss':abyss,**{key:[b for b in bosses if b['id']==bid] for key,bid in [('nymrissa',3379),('kithix',3513)]}}
    options=normalize(st.session_state.get('analysis_options'))
    configured='analysis_options' in st.session_state
    if configured:
        choice='configured'
        target=target_bosses(snapshot['zone'],options)
        st.caption(f"Ziel: {'Mythic' if options['difficulty']==5 else 'Heroic'} · Abyss {options['abyss']}/8 · Nymrissa {options['nymrissa']}/1 · Kith’ix {options['kithix']}/1 · {'Detailmodus' if options['detailed'] else 'Basismodus'}")
    else:
        choice=st.selectbox('Scoring-Ziel',[key for key,value in profiles.items() if value],key='score_target',
            format_func=lambda key:{'abyss':'The Venomous Abyss · 6/8 und 8/8','nymrissa':'Nymrissa · separat','kithix':'Kith’ix · separat'}[key])
        target=profiles[choice]
    if not target:
        st.info('Bitte mindestens einen Zielboss in den Einstellungen wählen.');return
    if snapshot['difficulty']!=options['difficulty'] and configured:
        st.info('Rankings für die gewählte Schwierigkeit werden noch geladen.');return
    histories=cached_histories(snapshot) if options['detailed'] else {}
    higher=cached_higher(snapshot) if options['detailed'] and options['difficulty']==4 else {}
    item_reference=options['item_reference'] if options['detailed'] else None
    snapshot=deepcopy(snapshot)
    for character in snapshot['characters']:
        character['scoring_gear']=cached_latest_gear(snapshot,character,target) if options['detailed'] else None
    mains={} # Main feature is dormant, including previously saved bonuses.
    full=group_score(snapshot,target,histories,mains,higher=higher,item_reference=item_reference)
    goals=[(f'{min(6,len(target))}/{len(target)}',target[:6]),(f'{len(target)}/{len(target)}',target)] if choice=='abyss' and len(target)>6 else [(f"{len(target)}/{len(target)} gewählte Bosse" if configured else '1/1',target)]
    columns=st.columns(len(goals))
    for column,(name,goal_bosses) in zip(columns,goals):
        goal=group_score(snapshot,goal_bosses,histories,mains,higher=higher,item_reference=item_reference)
        column.metric('Ziel '+name+(' · vorläufig' if goal['provisional'] else ''),'–' if goal['score'] is None else f"{goal['score']:.1f} / 100")
        column.caption(label(goal['score']))
        if goal['flags']:
            with column.expander('Hinweise zum Ziel '+name):
                for flag in goal['flags']:st.write(flag)
    st.caption(f"Nur {full['members']} aktuelle Gruppenmitglieder zählen zum Gruppenscore; Bewerber werden separat bewertet. Datenabdeckung: {full['coverage']:.0%} der ausgewählten Bossdaten. Kernheiler: {full['core_healers']} erforderlich, davon {full['strong_core_healers']} mit Score ≥70.")
    if full['provisional']:
        st.warning('Vorläufiger Gruppenscore: Rollenbesetzung oder Daten sind noch unvollständig. Vorhandene Rollen werden bewertet, fehlende Rollen nicht als ausreichend angenommen.')
    if st.session_state.get('_analysis_job'):
        st.caption('⏳ Zwischenstand – API-Abfragen laufen noch. Der Score wird nach Abschluss aktualisiert.')
    if full['components']:
        frame_table(pd.DataFrame([{'Bereich':key,'Score':round(value,1),'Gewicht':weight} for key,value,weight in
            [(key,value,f"{full['effective_weights'][key]*100:.1f} %") for key,value in full['components'].items()]]),key='scoring_components',percent_columns=['Score'])
    rows=[]
    for character in snapshot['characters']:
        score=full['players'][identity(character['name'],character['realm'])]
        rows.append({'Charakter':character['name'],'Status':'Bewerber' if character.get('applicant') else 'Gruppe',
            'Rolle':score['role'],'Score':score['score'],'Erfahrung':score['experience'],'Median':score['median'],
            'Best':score['best'],'Streuungsabzug':score['penalty'],
            'Log-Itemlevel':score['item_level'],'Itemlevel-Bonus':score['item_bonus'],'Höhere-Kills-Bonus':score['higher_bonus'],
            'Historie':f"{score['history_bosses']}/{score['bosses']} Bosse",'Trend':score['trend']})
    table=pd.DataFrame(rows)
    order=sorted(range(len(rows)),key=lambda i:(0 if snapshot['characters'][i].get('applicant') else 1,
        {'Tank':0,'Heal':1,'DD':2}.get(rows[i]['Rolle'],3),-(rows[i]['Score'] or 0)))
    from boss_ui import player_groups
    grouped=[{'Charakter':c['name'],'Server':c['realm'],'Rolle':rows[i]['Rolle'],'Best Ø':rows[i]['Best']} for i,c in enumerate(snapshot['characters'])]
    for group_label,indices in player_groups(grouped,snapshot):
        selected_order=[i for i in order if i in indices]
        with st.expander(group_label,expanded=True):
            frame_table(table.iloc[selected_order],key='scoring_players_'+str(indices[0]),percent_columns=['Score','Erfahrung','Median','Best'],
                character_indices=selected_order,open_character=open_character,region=snapshot['region'])
    st.download_button('Scoring CSV',table.to_csv(index=False).encode('utf-8-sig'),'raid_scoring.csv','text/csv')
    with st.expander('Bewertung erklären / Einzelspieler'):
        index=st.selectbox('Spieler im Scoring',list(range(len(rows))),format_func=lambda i:rows[i]['Charakter'],key='score_player')
        character=snapshot['characters'][index]
        detail=full['players'][identity(character['name'],character['realm'])]
        st.write(f"Modell {VERSION}: Erfahrung / Median / Best: Tanks 85/12/3 %, Heiler 70/25/5 %, DDs 55/35/10 %. Streuungsabzug maximal 4 / 8 / 20 Punkte.")
        st.caption('Itemlevel nur im Detailmodus: neuester verfügbarer Kill-Log der Zielbosse, nicht live aus dem Arsenal. Fehlende Werte ergeben keinen Bonus. Höhere Kills ebenfalls nur im Detailmodus; kein Abzug bei fehlenden Kills.')
        st.write('Kill-Erfahrung pro Boss: 1 → 28, 2 → 53, 3 → 72, 4 → 86, 5 → 95; weitere Kills nähern sich langsam 100. Bossgewichte steigen in WCL-Reihenfolge von 1 bis 3. 6/8 bezieht sich auf die ersten sechs Bosse dieser Reihenfolge.')
        st.write('Tanks: der schwächere Tank zählt innerhalb des Tankwerts stärker. Kernheiler: die besten ceil(Heilerzahl/2). DDs: der schwächere Teil fließt zusätzlich ein. Der Gruppenscore ist durch den schwächeren Tank +20 und Kernheiler +25 begrenzt.')
        st.caption('Der vollständige Raid kann konservativ nicht höher als das Teilziel 6 Bosse bewertet werden. Ein höherer Zahlenwert wäre keine plausible höhere Clear-Chance für das schwierigere Ziel.')
        st.caption('Best–Median-Abstand ist nur ein Streuungs-Indiz. Niedrige Logs beweisen keine Tode. Heiler-Logs hängen stark vom Heilbedarf ab. Fehlende öffentliche Daten sind keine Aussage über fehlende Kills.')
        for flag in detail['flags']:st.caption(flag)
        frame_table(pd.DataFrame(detail['details']),key='scoring_details',percent_columns=['Erfahrung','Median','Best'])
    if st.session_state.get('scoring_notice'):st.info(st.session_state['scoring_notice'])


def render_manual_mains(snapshot,token_provider,test_mode,rows):
    with st.expander('Optional: Main-/Warband-Erfahrung zuordnen'):
        st.caption('Deaktivierte manuelle Main-Zuordnung. WCL liefert Killzahlen; keine Besitzprüfung.')
        st.caption('WCL-Main-Killzahlen gelten für die aktuell gewählte Raidzone und Schwierigkeit. Bis zu fünf fehlende Mainprofile werden gemeinsam geladen; unbekannte Heiler können eine zusätzliche HPS-Korrektur benötigen. Keine Summierung mehrerer Mains: Nur der stärkste passende Erfahrungsbonus zählt.')
        alt=st.selectbox('Zu bewertender Charakter',list(range(len(rows))),format_func=lambda i:rows[i]['Charakter'],key='score_main_alt')
        references=assignments().get(assignment_key(snapshot,snapshot['characters'][alt]),[])
        st.caption('Gespeicherte Zuordnung: '+(', '.join(f"{r['name']}-{r['realm']}" for r in references) or 'keine'))
        text=st.text_area('Main-/Warband-Charaktere: je Zeile Name-Server oder Profil-Link (maximal 5)',key='score_main_refs')
        refresh=st.checkbox('Gespeicherte Zusatzdaten aktualisieren',key='score_refresh_main',help='Ohne Aktualisierung werden bis zu 24 Stunden alte Daten wiederverwendet.')
        if st.button('Main-Killzahlen aus WCL laden',disabled=test_mode or not text.strip(),key='score_load_main'):
            try:
                refs=[parse_reference(line,snapshot['region']) for line in text.splitlines() if line.strip()]
                unique={identity(r['name'],r['realm']):r for r in refs}
                if not 1<=len(unique)<=5:raise ValueError('Bitte ein bis fünf unterschiedliche Charaktere zuordnen.')
                current=snapshot['characters'][alt]
                if identity(current['name'],current['realm']) in unique:
                    raise ValueError('Der aktuelle Charakter kann nicht sein eigener Main-Bonus sein.')
                load_mains(snapshot,list(unique.values()),token_provider,refresh)
                saved=assignments();saved[assignment_key(snapshot,snapshot['characters'][alt])]=list(unique.values());save_json(ASSIGNMENTS,saved)
                st.session_state['scoring_notice']='Main-Killzahlen geladen; Bonus wird transparent angezeigt.'
                st.rerun()
            except Exception as error:st.error(str(error))
        if st.button('Main-Zuordnung entfernen',disabled=not references,key='score_remove_main'):
            saved=assignments();saved.pop(assignment_key(snapshot,snapshot['characters'][alt]),None);save_json(ASSIGNMENTS,saved);st.rerun()


def render_weekly_warning(snapshot,token_provider,test_mode):
    options=normalize(st.session_state.get('analysis_options'))
    if not options['scoring'] or not options['detailed']:return
    if snapshot['difficulty']!=options['difficulty']:return
    target=target_bosses(snapshot['zone'],options)
    selected={**snapshot,'zone':{**snapshot['zone'],'encounters':target}}
    histories=cached_histories(snapshot)
    found=current_week_kills(selected,histories)
    if found:
        names=list(dict.fromkeys(r['Charakter'] for r in found))
        st.warning('Bosskills in der aktuellen ID nachgewiesen: '+', '.join(names)+'.')
        with st.expander('Nachgewiesene ID-Kills'):
            frame_table(pd.DataFrame(found).reindex(columns=['Charakter','Boss','Log']),key='weekly_kills',link_columns=['Log'])
            missing=[row for row in found if not row.get('Log')]
            if missing:
                st.caption(f'{len(missing)} Log-Verweise fehlen im älteren Cache. Beim Nachladen wird nur die betreffende Spieler-/Boss-Historie erneut angefragt.')
                if st.button('Fehlende Log-Links nachladen',disabled=test_mode,key='weekly_load_links'):
                    errors=[]
                    with st.spinner('Log-Verweise nachladen …'):
                        for character in snapshot['characters']:
                            bosses=[b for b in target if any(r['Charakter']==character['name'] and r['Boss']==b['name'] for r in missing)]
                            if bosses:errors+=load_histories(snapshot,[character],bosses,token_provider,refresh=True)
                    if errors:st.error('; '.join(errors))
                    else:st.rerun()
    window=weekly_window(snapshot['region'])
    if window:
        from datetime import datetime, timezone
        start,end=[datetime.fromtimestamp(t,timezone.utc).strftime('%d.%m. %H:%M UTC') for t in window]
        st.caption(f'ID: {start} bis {end}. Nur gewählte Zielbosse und Schwierigkeit, keine Extra-Aufrufe. Fehlende Warnung bedeutet keinen Nachweis einer freien ID; öffentliche Logs können fehlen. Normal-/Heroic-Kills verhindern nicht grundsätzlich den Gruppenbeitritt.')
