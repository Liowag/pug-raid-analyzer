# WoW Raid Analyzer

Streamlit-Anwendung für Raider.IO-Gruppenexporte und öffentliche Warcraft-Logs-Rankings.

## Nutzung

1. Eigenen WCL-API-Client unter https://www.warcraftlogs.com/api/clients anlegen.
2. In der App eigene Client ID und eigenes Client Secret eingeben und verbinden.
3. `/rio group`-Export einfügen, Ziel und gewünschten Detailumfang wählen.

Ohne Zugangsdaten stehen anonymisierte Testdaten zur Verfügung. Die App verwendet
keine gemeinsamen WCL-Zugangsdaten und keine lokalen Schlüsseldateien als Vorgabe.

## Datenverarbeitung

- Client ID, Client Secret und OAuth-Token werden nur im Arbeitsspeicher der
  jeweiligen Sitzung gehalten. Sie werden an den App-Server und an WCL übertragen.
- Gruppenlisten, Einstellungen, API-Protokolle und Detailcaches werden pro Sitzung
  getrennt verarbeitet. Persönliche Laufzeitdaten werden nicht in Dateien gespeichert.
- Browser-Neuladen, getrennte Browser-Sitzung oder Server-Neustart können die
  Anmeldung und gespeicherte Auswertung zurücksetzen. Keine dauerhafte Speicherung
  oder Anmeldung im Browser. Mit „Zugangsdaten und Sitzungsdaten vergessen“ kann
  die Sitzung geleert werden. Bereits laufende HTTP-Aufrufe können noch enden.
- Caches sind begrenzt; beim Verdrängen älterer Einträge können weitere Calls nötig werden.
- Öffentliche Rankings sind kein vollständiger Nachweis einer Ingame-ID.
  Scoring ist eine experimentelle Heuristik, keine Erfolgswahrscheinlichkeit.
- Logos und Bossbilder bleiben Eigentum ihrer jeweiligen Rechteinhaber;
  Quellen sind in `assets/` dokumentiert.

## Lokal starten

Python **3.12** verwenden, danach:

```sh
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

## Veröffentlichung

Das bestehende Projektverzeichnis wird direkt als Git-Repository genutzt.
Die `.gitignore` schließt lokale Zugangsdaten, Auswertungen, Caches,
Entwicklerwerkzeuge und alte Exportpakete aus. Keine separate Release-Kopie nötig.

Die vollständige Anleitung steht in [DEPLOYMENT.md](DEPLOYMENT.md).
Einstiegsdatei: **app.py**. Es sind **keine Betreiber-WCL-Secrets** in Streamlit
Cloud einzutragen. Jeder Nutzer verbindet seinen eigenen WCL-Zugang.
