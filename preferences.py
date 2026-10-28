"""Small persisted UI preferences; no credentials are stored here."""
import json
from analyzer import DATA, save_json

PATH=DATA/'ui_settings.json'


def load_preferences():
    try:
        data=json.loads(PATH.read_text(encoding='utf-8'))
    except (OSError,ValueError):
        data={}
    value=data.get('include_applicants',True) if isinstance(data,dict) else True
    return {'include_applicants':value if isinstance(value,bool) else True}


def save_preferences(include_applicants):
    save_json(PATH,{'include_applicants':bool(include_applicants)})


def selected_roster(entries, include_applicants):
    return list(entries) if include_applicants else [entry for entry in entries if not entry.get('applicant',False)]
