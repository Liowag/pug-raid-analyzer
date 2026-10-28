"""Small native-page components: paste listener and clickable boss navigation."""
import base64
import uuid
from pathlib import Path
import streamlit as st
from streamlit.components.v2 import component

ROOT = Path(__file__).parent
table_component = component('raid_table', html='<div class="scroll"><table></table></div>', css='''
.scroll {overflow-x:auto;border:1px solid #343842}
table {border-collapse:collapse;width:100%;font:13px Arial,sans-serif;color:#ddd}
th {background:#222226;font-weight:500;color:#eee;text-align:right;white-space:nowrap}
td,th {padding:5px 8px;border-right:1px solid #34343b;border-bottom:1px solid #34343b;height:18px}
td {text-align:right;white-space:nowrap} td:first-child,th:first-child {text-align:left}
tbody tr:nth-child(odd) {background:#101116} tbody tr:nth-child(even) {background:#1c1d24}
tbody tr:hover {background:#2b2333}
button {font:inherit;cursor:pointer;color:#a4c5ff;background:none;border:0;padding:0;text-align:left}
button:hover {color:#d2a8ff;text-decoration:underline}
button.pin {border:1px solid #73518a;border-radius:3px;padding:1px 7px;margin:0 5px 3px 0;color:#d4b9ed}
button.sort {color:#eee;width:100%;text-align:inherit;white-space:nowrap}
button.sort:focus-visible,a.profile:focus-visible {outline:2px solid #b68bd4;outline-offset:2px}
button.sort .arrow {color:#9f8aad;font-size:11px;margin-left:6px}
a.profile {display:inline-flex;width:23px;height:22px;align-items:center;justify-content:center;
 border:1px solid #4d4357;border-radius:3px;margin:0 2px;vertical-align:middle;text-decoration:none;background:#17141b}
a.profile:hover {background:#36283e;border-color:#ab80c4}
a.profile img {width:17px;height:17px;object-fit:contain}
a {color:#a4c5ff} .percent {font-weight:bold}
''', js='''
export default function({data,parentElement,setTriggerValue}) {
 const table=parentElement.querySelector('table'); table.replaceChildren();
 const collator=new Intl.Collator('de',{sensitivity:'base',numeric:true});
 let sorting=null;
 try {sorting=JSON.parse(sessionStorage.getItem(data.storageKey));} catch {}
 if(!sorting || !data.headers.some(h=>h.label===sorting.label && h.sortable!==false)) sorting=null;
 const head=document.createElement('thead'), hr=document.createElement('tr');
 const buttons=[];
 for(const [index,h] of data.headers.entries()) {
   const th=document.createElement('th');
   th.scope='col';
   if(h.pin) {const b=document.createElement('button'); b.className='pin'; b.textContent=h.active?'✓':'+';
     b.title=h.active?'Aus Übersicht entfernen':'In Übersicht anzeigen';
     b.onclick=()=>setTriggerValue('action',{kind:'pin',field:h.pin}); th.append(b);}
   if(h.sortable!==false) {
     const button=document.createElement('button');button.className='sort';button.title=h.label+' sortieren';
     const label=document.createElement('span');label.textContent=h.label;
     const arrow=document.createElement('span');arrow.className='arrow';arrow.setAttribute('aria-hidden','true');
     button.append(label,arrow);
     button.onclick=()=>{
       const numeric=data.rows.some(row=>typeof row[index]?.sort==='number');
       sorting={label:h.label,direction:sorting?.label===h.label?(sorting.direction==='asc'?'desc':'asc'):(numeric?'desc':'asc')};
       try {sessionStorage.setItem(data.storageKey,JSON.stringify(sorting));} catch {}
       draw();
     };
     th.append(button);buttons.push({th,arrow,label:h.label});
   } else th.append(document.createTextNode(h.label));
   hr.append(th);
 }
 head.append(hr);table.append(head);const body=document.createElement('tbody');
 table.append(body);
 function value(cell) {
   if(!cell)return null;
   const v=Object.hasOwn(cell,'sort')?cell.sort:cell.text;
   return v===undefined || v===null || v==='' || v==='–' || (typeof v==='number'&&!Number.isFinite(v))?null:v;
 }
 function icon(kind) {
   const img=document.createElement('img');img.src=data.icons[kind];img.alt='';img.setAttribute('aria-hidden','true');
   return img;
 }
 function draw() {
   for(const {th,arrow,label} of buttons) {
     const active=sorting?.label===label;
     th.setAttribute('aria-sort',active?(sorting.direction==='asc'?'ascending':'descending'):'none');
     arrow.textContent=active?(sorting.direction==='asc'?'▲':'▼'):'↕';
   }
   const rows=data.rows.map((cells,index)=>({cells,index}));
   const column=sorting?data.headers.findIndex(h=>h.label===sorting.label):-1;
   if(column>=0)rows.sort((a,b)=>{
     const x=value(a.cells[column]),y=value(b.cells[column]);
     if(x===null||y===null)return x===y?a.index-b.index:x===null?1:-1;
     const cmp=typeof x==='number'&&typeof y==='number'?x-y:collator.compare(String(x),String(y));
     return (sorting.direction==='asc'?cmp:-cmp)||a.index-b.index;
   });
   body.replaceChildren();
 for(const {cells} of rows) {
   const tr=document.createElement('tr');
   for(const cell of cells) {
     const td=document.createElement('td'); let el;
     if(cell.links) {
       for(const link of cell.links) {
         const a=document.createElement('a');a.className='profile';a.href=link.url;a.target='_blank';a.rel='noopener noreferrer';
         a.title=link.label;a.setAttribute('aria-label',link.label);a.append(icon(link.icon));td.append(a);
       }
       tr.append(td);continue;
     }
     if(cell.character!==undefined) {el=document.createElement('button');el.onclick=()=>setTriggerValue('action',{kind:'character',index:cell.character});}
     else if(cell.url) {el=document.createElement('a');el.href=cell.url;el.target='_blank';el.rel='noopener noreferrer';}
     else el=document.createElement('span');
     el.textContent=cell.text; if(cell.color) {el.style.color=cell.color;el.className='percent';}
     if(cell.title)el.title=cell.title;td.append(el);tr.append(td);
   } body.append(tr);
 }
 }
 draw();
}''')


def official_icons():
    result={}
    for name,extension,mime in [('wcl','png','image/png'),('rio','ico','image/x-icon')]:
        path=ROOT/'assets'/'logos'/(name+'.'+extension)
        result[name]='data:'+mime+';base64,'+base64.b64encode(path.read_bytes()).decode()
    return result


def render_table(headers, rows, key, on_action=None):
    def action():
        if on_action:
            on_action(st.session_state[key].action)
    namespace=st.session_state.setdefault('_table_sort_namespace',uuid.uuid4().hex)
    return table_component(key=key,data={'headers':[{'label':h} if isinstance(h,str) else h for h in headers],
                                          'storageKey':f'raid_sort_{namespace}_{key}',
                                          'rows':rows,'icons':official_icons()},on_action_change=action)


def frame_table(frame, key, percent_columns=(), character_indices=None, open_character=None, region=None, link_columns=()):
    from rankings import percentile_style, profile_links
    import pandas as pd
    rows=[]
    for position,(_,row) in enumerate(frame.iterrows()):
        cells=[]
        for column,value in row.items():
            missing=pd.isna(value)
            text='–' if missing else f'{value:.3f}' if isinstance(value,float) and column in ('Dauer (s)','Punkte') else f'{value:.1f}' if isinstance(value,float) else str(value)
            cell={'text':text}
            cell['sort']=None if missing else value.item() if hasattr(value,'item') else value
            if column in link_columns and not missing:
                cell['url']=str(value);cell['text']='↗ Log öffnen'
            if column=='Schnellster Kill' and not missing and isinstance(value,str):
                parts=value.split(':')
                if len(parts)==2 and all(part.isdigit() for part in parts):
                    cell['sort']=int(parts[0])*60+int(parts[1])
            if column in percent_columns and not missing:
                cell['color']=percentile_style(value).split(';')[0].removeprefix('color: ')
            if column=='Charakter' and character_indices is not None:
                cell['character']=character_indices[position]
            cells.append(cell)
            if column=='Charakter' and character_indices is not None and region:
                # Profile URLs use the original roster's server, not the display name.
                snapshot=st.session_state.get('snapshot')
                character=snapshot['characters'][character_indices[position]]
                cells.append(profile_links(character['name'],character['realm'],region))
        rows.append(cells)
    headers=list(frame.columns)
    if 'Charakter' in headers and character_indices is not None and region:
        headers.insert(headers.index('Charakter')+1,{'label':'Links','sortable':False})
    return render_table(headers,rows,key,
                        lambda action: open_character(action['index']) if action['kind']=='character' and open_character else None)


paste_component = component('rio_global_paste', html='<span></span>', js='''
export default function({setTriggerValue}) {
 const listener = event => {
   const text = event.clipboardData?.getData('text/plain');
   if (!text) return;
   try {
     const value = JSON.parse(text.replace(/^\\uFEFF/, ''));
     if (!value || typeof value.region !== 'string' ||
         !(Array.isArray(value.group) || Array.isArray(value.queue))) return;
   } catch { return; }
   event.preventDefault();
   setTriggerValue('paste', text);
 };
 document.addEventListener('paste', listener, true);
 return () => document.removeEventListener('paste', listener, true);
}''')

nav_component = component('raid_navigation', html='<nav></nav>', css='''
nav {display:grid;gap:4px;font:13px Arial,sans-serif}
button {display:flex;align-items:center;gap:8px;text-align:left;color:#ddd;background:#161219;
 border:1px solid #44384b;border-radius:5px;padding:6px;width:100%;cursor:pointer;min-height:36px}
button:hover,button.active {background:#35233e;border-color:#9770af;color:white}
img {width:25px;height:25px;border-radius:3px}
''', js='''
export default function({data,parentElement,setTriggerValue}) {
 const nav = parentElement.querySelector('nav'); nav.replaceChildren();
 for (const item of data.items) {
   const button = document.createElement('button');
   button.className = item.active ? 'active' : '';
   if (item.image) { const image=document.createElement('img'); image.src=item.image; image.alt=''; button.append(image); }
   const label=document.createElement('span'); label.textContent=item.name; button.append(label);
   button.onclick=()=>setTriggerValue('select', {id:item.id}); nav.append(button);
 }
}''')

def mount_paste(callback):
    paste_component(key='rio_global_paste', on_paste_change=callback, height=0)


def boss_navigation(snapshot, callback):
    def selected():
        callback(st.session_state['raid_nav'].select['id'])
    items=[{'id': None, 'name': 'Übersicht', 'active': st.session_state.get('view','Raidübersicht')=='Raidübersicht'}]
    for boss in snapshot['zone']['encounters']:
        path=ROOT/'assets'/'bosses'/f"{boss['id']}.jpg"
        items.append({'id':boss['id'],'name':boss['name'],
                      'active':st.session_state.get('view')=='Bossfokus' and st.session_state.get('_boss_id')==boss['id'],
                      'image':'data:image/jpeg;base64,'+base64.b64encode(path.read_bytes()).decode() if path.exists() else None})
    nav_component(key='raid_nav',data={'items':items},on_select_change=selected)
