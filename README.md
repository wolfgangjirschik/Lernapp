# Lern-App (Probeversion)

Du lädst Vorlesungsfolien (PDF) und eigene Notizen (PDF, TXT, MD) hoch.
Die App erstellt daraus mit Claude:

- eine **Zusammenfassung** (auch zum Herunterladen),
- **Karteikarten** (umdrehen, vor und zurück),
- ein **Quiz** mit Punktzahl. Du stellst ein, welche Fragetypen du willst:
  Single Choice (eine Antwort richtig), Multiple Choice (mehrere richtig)
  und offene Fragen. Deine offenen Antworten bewertet Claude mit Feedback.

Die App läuft auf deinem Computer und öffnet sich im Browser.

---

## Was du brauchst

1. **Python 3.11 oder neuer** (empfohlen: die neueste Version 3.13).
2. **Einen Claude API-Schlüssel** von https://console.anthropic.com → „API Keys“.
   Lade dort ein Guthaben auf und setze unter „Limits“ ein Ausgabenlimit.

---

## Einmalig installieren

### Windows

1. Python herunterladen: https://www.python.org/downloads/ → Installer starten.
   **Wichtig:** Unten im ersten Fenster den Haken bei **„Add python.exe to PATH“** setzen.
2. Den Ordner `lern-app` z. B. nach `Dokumente` kopieren.
3. Den Ordner im Explorer öffnen, oben in die Adresszeile klicken, `cmd` eintippen, Enter.
   Es öffnet sich ein schwarzes Fenster (Eingabeaufforderung) direkt in diesem Ordner.
4. Diese Befehle nacheinander eintippen (jeweils Enter):

   ```
   python -m venv venv
   venv\Scripts\activate
   pip install -r requirements.txt
   ```

### Mac

1. Python herunterladen: https://www.python.org/downloads/ → Installer (.pkg) starten.
2. Den Ordner `lern-app` z. B. nach `Dokumente` kopieren.
3. Programm **Terminal** öffnen (Spotlight: Cmd + Leertaste, „Terminal“).
4. `cd ` eintippen (mit Leerzeichen), den Ordner `lern-app` aus dem Finder ins Terminal ziehen, Enter.
5. Diese Befehle nacheinander eintippen (jeweils Enter):

   ```
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```

---

## App starten (jedes Mal)

Fenster im Ordner `lern-app` öffnen (wie oben, Schritt 3 bzw. 3–4), dann:

- **Windows:**
  ```
  venv\Scripts\activate
  streamlit run app.py
  ```
- **Mac:**
  ```
  source venv/bin/activate
  streamlit run app.py
  ```

Der Browser öffnet sich mit der App (Adresse `http://localhost:8501`).
Beim allerersten Start fragt Streamlit evtl. nach einer E-Mail-Adresse: einfach Enter drücken.
**Beenden:** Im schwarzen Fenster `Strg + C` drücken.

---

## So benutzt du die App

1. Links den API-Schlüssel eingeben (wird nicht gespeichert, nur für diese Sitzung benutzt).
2. Folien und Notizen hochladen. **PowerPoint** vorher als PDF speichern
   (Datei → Speichern unter → PDF).
3. Tab wählen und auf „… erstellen“ klicken. Bei langen Unterlagen dauert es 1–2 Minuten.

**Modell:** Opus 5.5 liefert die beste Qualität. Sonnet 5.5 kostet etwa halb so viel,
Haiku 5.5 deutlich weniger. Preise: https://claude.com/pricing

**Grenzen:** Pro Anfrage höchstens 32 MB und 600 PDF-Seiten.


---

## Ordner und gespeicherte Dokumente

Links unter „Meine Ordner“ legst du Ordner an (z. B. pro Fach). Wenn ein Ordner
geöffnet ist, werden neue Zusammenfassungen, Karteikarten und Quizze automatisch
darin gespeichert, beim Quiz auch dein Ergebnis. Jedes Dokument kannst du öffnen,
umbenennen, in einen anderen Ordner verschieben oder löschen.

Alles wird auf deinem Computer gespeichert, im Ordner `lern-app/meine_daten`
(Datei `lern_daten.json`). Wenn du den Ordner sicherst, sicherst du alle Dokumente.

**Hinweis für später:** Wenn die App über Streamlit Community Cloud geteilt wird,
bleibt so eine Datei dort nicht dauerhaft erhalten. Dafür bräuchte man eine
Online-Datenbank. (Eigene Einschätzung, keine offizielle Quelle.)

---

## Optional: Eigenen Schlüssel fest hinterlegen

Damit du ihn nicht jedes Mal eingeben musst: Im Ordner `lern-app` einen Unterordner
`.streamlit` anlegen und darin eine Datei `secrets.toml` mit diesem Inhalt:

```
ANTHROPIC_API_KEY = "sk-ant-...dein-schlüssel..."
```

**Achtung:** Diese Datei niemals weitergeben oder ins Internet hochladen.

---

## Quellen

- Streamlit starten (`streamlit run`): https://docs.streamlit.io/get-started/installation
- Streamlit Secrets (`secrets.toml`): https://docs.streamlit.io/develop/concepts/connections/secrets-management
- Claude liest PDFs (Limits 32 MB / 600 Seiten): https://platform.claude.com/docs/en/build-with-claude/pdf-support
- Strukturierte Antworten (für Karteikarten/Quiz): https://platform.claude.com/docs/en/build-with-claude/structured-outputs
- Python-Installation unter Windows („Add to PATH“): https://docs.python.org/3/using/windows.html
