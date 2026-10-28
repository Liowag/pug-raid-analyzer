# Schritt für Schritt zu Streamlit Community Cloud

Stand: 28.10.2026. Die Dateien sind für Python 3.12 vorbereitet.
Diese Anleitung veröffentlicht noch nichts automatisch.

## 1. Das vorhandene Projektverzeichnis verwenden

Es ist keine ZIP-Datei und kein separater Release-Ordner mehr erforderlich.
Das bestehende Projektverzeichnis mit `app.py` wird direkt zum Git-Repository.
Die `.gitignore` trennt die zu veröffentlichenden Dateien von lokalen Daten.

Im Repository enthalten bleiben App-Code, Tests, Bilder/Logos, `start.bat`,
`requirements.txt`, Dokumentation sowie diese beiden öffentlichen Datendateien:

- `data/raid_zones_seed.json`
- `data/public_demo.json`

Lokal erhalten, aber ignoriert bleiben unter anderem `wclogsclient.txt`,
`.venv/`, `.tmp/`, `.analyze/`, `artifacts/`, `release/` und alle übrigen
Dateien unter `data/`. Auch ältere Capture-/Profiling-Skripte und `README.txt`
bleiben als lokale Entwicklerdateien erhalten. Tests nutzen auf einem frischen
Checkout die mitgelieferten anonymisierten Demodaten.

Wichtig: Eine `.gitignore` wirkt bei **Git**, nicht beim manuellen Hochladen
von Dateien über den GitHub-Browser. Deshalb für diesen Weg Git oder GitHub
Desktop verwenden und nicht den gesamten Ordner ins Browser-Uploadfeld ziehen.

## 2. Leeres GitHub-Repository erstellen

1. Bei https://github.com anmelden.
2. **+ → New repository** öffnen.
3. Einen Namen wählen, beispielsweise `wow-raid-analyzer`.
4. **Public** wählen, wenn der Quellcode öffentlich sein darf. Alternativ Private
   verwenden und Community Cloud später Zugriff gewähren.
5. **Keine** README, Lizenz oder `.gitignore` automatisch erzeugen lassen:
   Das Repository soll für den ersten Push leer sein.
6. **Create repository** anklicken und die HTTPS-Adresse kopieren, beispielsweise
   `https://github.com/DEIN-NAME/wow-raid-analyzer.git`.

## 3. Den bestehenden Ordner mit Git veröffentlichen

Git muss installiert sein. PowerShell im vorhandenen Projektordner öffnen
(dort, wo `app.py` liegt) und zunächst ausführen:

```powershell
git init -b main
git status --short
git add --dry-run .
```

Die Vorschau prüfen: Es dürfen keine Zugangsdaten, `.venv`, Live-Auswertungen,
Logs, `artifacts/`, `.tmp/` oder `release/` auftauchen. Danach:

```powershell
git add .
git diff --cached --name-only
git commit -m "Prepare Streamlit Community Cloud app"
git remote add origin https://github.com/DEIN-NAME/wow-raid-analyzer.git
git push -u origin main
```

Die Remote-Adresse durch deine tatsächliche Adresse ersetzen. Git fragt ggf.
nach GitHub-Anmeldung. Falls Git noch keinen Commit-Namen und keine E-Mail kennt,
diese zunächst für das lokale Repository konfigurieren; dafür kann eine
GitHub-Noreply-Adresse verwendet werden.

Falls der Ordner bereits ein Git-Repository ist, `git init` überspringen und den
vorhandenen Branch/Remote prüfen, statt sie ungeprüft zu überschreiben.
Bereits versionierte Dateien werden durch `.gitignore` nicht nachträglich entfernt.
Falls vorher versehentlich ein Secret veröffentlicht wurde: zuerst bei WCL
ersetzen/widerrufen; bloßes Löschen aus dem aktuellen Commit reicht dann nicht.

Danach im GitHub-Repository kontrollieren:

```text
app.py
auth_ui.py
session_storage.py
requirements.txt
style.css
.streamlit/config.toml
assets/bosses/...
assets/logos/...
data/raid_zones_seed.json
data/public_demo.json
tests/...
... weitere App-Module
```

`app.py` und `requirements.txt` müssen direkt im Hauptordner liegen.
GitHub Pages muss nicht aktiviert werden.

## 4. Community Cloud mit GitHub verbinden

1. https://share.streamlit.io öffnen und anmelden.
2. GitHub verbinden und Zugriff auf das gewählte Repository erlauben.
3. **Create app** anklicken und das vorhandene GitHub-Repository auswählen
   (in der Oberfläche ggf. „Yup, I have an app“).
4. Folgendes einstellen:
   - **Repository:** dein Konto / `wow-raid-analyzer`
   - **Branch:** `main` bzw. der tatsächlich angelegte Branch
   - **Main file path:** `app.py`
   - **App URL:** optional einen freien, verständlichen Namen wählen
5. Unter **Advanced settings** Python **3.12** auswählen.
6. **Secrets leer lassen.** Weder deine Client ID noch dein Client Secret hier
   hinterlegen. Die Nutzer melden sich innerhalb der Anwendung individuell an.
7. **Deploy** anklicken und den Installations-/Startvorgang abwarten.

Community Cloud installiert die Pakete aus `requirements.txt` automatisch.
Es ist kein zusätzlicher Build-Befehl, Startskript oder GitHub-Pages-Setup nötig.

## 5. Funktionsprüfung vor dem Teilen

1. Die neue `https://...streamlit.app`-Adresse öffnen.
2. Kontrollieren, dass Client ID und Secret zunächst leer sind.
3. **Testdaten nutzen** wählen: Übersicht und Scoring müssen ohne Zugang funktionieren.
4. Deinen eigenen WCL-Zugang eingeben und **Zugang prüfen und verbinden** anklicken.
5. Eine kleine echte `/rio group`-Liste einfügen; zuerst ohne Detailmodus testen.
6. Danach bei Bedarf Detailmodus und API-Budgetanzeige prüfen.
7. Dieselbe URL in einem privaten Browserfenster öffnen: Dort dürfen deine
   Zugangsdaten, Gruppenliste und Auswertung nicht bereits vorhanden sein.
8. **Zugangsdaten und Sitzungsdaten vergessen** testen: Eingaben und Auswertung
   verschwinden nur in der betreffenden Sitzung.
9. Falls die App für alle zugänglich sein soll, in den Community-Cloud-
   Freigabe-/Zugriffseinstellungen öffentlichen Zugriff einstellen. Private
   Repository-Sichtbarkeit und App-Zugriff sind unterschiedliche Einstellungen.
10. Nun die App-URL an andere weitergeben.

## 6. Was deine Nutzer benötigen

Jeder Nutzer legt unter https://www.warcraftlogs.com/api/clients einen eigenen
API-Client an. Die öffentliche Datenabfrage verwendet den Client-Credentials-
Flow: Client ID und Client Secret, ohne OAuth-Rückleitung in unsere Anwendung.
Wenn WCL eine Redirect-URI verlangt, kann der Nutzer die veröffentlichte App-URL
eintragen; der aktuelle Ablauf verwendet sie nicht als Login-Callback.

Die Eingabe erfolgt ausschließlich in der App. Ein WCL-Account-Login allein
ersetzt diesen eigenen API-Client nicht. Andere Nutzer dürfen nicht deine
Betreiber-Schlüssel verwenden. Zugangsdaten nie per Chat oder GitHub-Issue senden.

## 7. Updates und typische Probleme

- Für Updates im selben Projektordner `git add .`, `git diff --cached --name-only`,
  `git commit -m "Update app"` und `git push` verwenden. Community Cloud
  übernimmt Änderungen aus demselben Branch automatisch.
- **ModuleNotFoundError:** Kontrollieren, ob alle mitgelieferten Python-Module
  und `requirements.txt` im Repository liegen. Keine Dateien nur einzeln erraten.
- **Fehlende Bilder oder JSON-Dateien:** `assets/` und beide öffentlichen `data/`-
  Dateien mitsamt ihren Unterordnern hochladen.
- **Paketinstallation schlägt fehl:** Python 3.12 und mitgelieferte Requirements
  prüfen, anschließend den Cloud-Buildlog lesen.
- **401/403 beim Verbinden:** Der betreffende Nutzer prüft seine eigene Client ID
  und sein Secret. Kein Betreiber-Schlüssel als Ersatz hinterlegen.
- **App schläft oder startet neu:** Community Cloud hat Ressourcenlimits und
  kann inaktive Apps schlafen legen. Nach Neustart müssen Nutzer ggf. neu
  verbinden und ihre Gruppe erneut einfügen.
- Die gemeinsame Serverkapazität bleibt begrenzt, obwohl jeder sein eigenes
  WCL-Budget verwendet. Für viele gleichzeitige Nutzer erst reale Last messen;
  gegebenenfalls später auf einen eigenen Server wechseln.

## Offizielle Quellen

- Deployment: https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/deploy
- Dateien: https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/file-organization
- Abhängigkeiten: https://docs.streamlit.io/deploy/concepts/dependencies
- Limits: https://docs.streamlit.io/deploy/streamlit-community-cloud/manage-your-app
- Sitzungsverhalten: https://docs.streamlit.io/develop/api-reference/caching-and-state/st.session_state
- WCL-Authentifizierung: https://www.warcraftlogs.com/api/docs
