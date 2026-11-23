"""Lern-App (Probeversion)

Lade Vorlesungsfolien (PDF) und eigene Notizen (PDF, TXT, MD) hoch.
Claude erstellt daraus eine Zusammenfassung, Karteikarten und ein Quiz.

Start:  streamlit run app.py
"""

import base64
import copy
import html
import json
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Literal

import anthropic
import streamlit as st
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Einstellungen
# ---------------------------------------------------------------------------

MODELLE = {
    "Claude Opus 5.5 (beste Qualität)": "claude-opus-5-5",
    "Claude Sonnet 5.5 (günstiger)": "claude-sonnet-5-5",
    "Claude Haiku 5.5 (am günstigsten)": "claude-haiku-5-5",
}

# Für diese Modelle gibt es einen automatischen Ersatz, falls Claude eine
# Anfrage aus Sicherheitsgründen ablehnt ("server-side fallback").
MODELLE_MIT_FALLBACK = {"claude-opus-5-5", "claude-sonnet-5-5"}

MAX_TOKENS = 16000


# ---------------------------------------------------------------------------
# Datenformate für Karteikarten und Quiz (Claude antwortet genau in diesem Format)
# ---------------------------------------------------------------------------

class Karteikarte(BaseModel):
    vorderseite: str  # Frage oder Begriff
    rueckseite: str   # Antwort oder Erklärung


class Karteikarten(BaseModel):
    karten: List[Karteikarte]


FRAGETYPEN = {
    "single": "Single Choice (genau eine Antwort richtig)",
    "multiple": "Multiple Choice (eine oder mehrere Antworten richtig)",
    "offen": "Offene Fragen (eigene Antwort schreiben)",
}


class QuizFrage(BaseModel):
    typ: Literal["single", "multiple", "offen"]
    frage: str
    antworten: List[str]            # Antwortmöglichkeiten (bei offenen Fragen leer)
    richtige_antworten: List[int]   # Nummern der richtigen Antworten (0 = erste)
    musterloesung: str              # vor allem für offene Fragen
    erklaerung: str


class Quiz(BaseModel):
    fragen: List[QuizFrage]


class Bewertung(BaseModel):
    frage_nr: int
    ergebnis: Literal["richtig", "teilweise richtig", "falsch"]
    feedback: str


class Bewertungen(BaseModel):
    bewertungen: List[Bewertung]


PUNKTE = {"richtig": 1.0, "teilweise richtig": 0.5, "falsch": 0.0}

TYP_SYMBOL = {"single": "🎯", "multiple": "☑️", "offen": "✍️"}
TYP_TITEL = {"single": "Single Choice", "multiple": "Multiple Choice", "offen": "Offene Fragen"}
TYP_TEXT = {
    "single": "Genau eine Antwort ist richtig.",
    "multiple": "Eine oder mehrere Antworten sind richtig.",
    "offen": "Du antwortest in eigenen Worten, Claude bewertet.",
}
TYP_KACHEL = {t: f"{TYP_SYMBOL[t]} {TYP_TITEL[t]}" for t in TYP_SYMBOL}


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------

def api_schluessel_vorbelegt() -> str:
    """Liest einen fest hinterlegten Schlüssel (optional).

    Reihenfolge: .streamlit/secrets.toml, dann Umgebungsvariable.
    """
    try:
        if "ANTHROPIC_API_KEY" in st.secrets:
            return st.secrets["ANTHROPIC_API_KEY"]
    except Exception:
        pass  # keine secrets.toml vorhanden
    return os.environ.get("ANTHROPIC_API_KEY", "")


def dateien_zu_bloecken(dateien, notizen_text: str) -> list:
    """Wandelt hochgeladene Dateien in Inhalte um, die Claude lesen kann."""
    bloecke = []
    for datei in dateien:
        daten = datei.getvalue()
        if datei.name.lower().endswith(".pdf"):
            # Claude kann PDFs direkt lesen (Text und Bilder der Folien).
            bloecke.append({
                "type": "document",
                "source": {
                    "type": "base64",
                    "media_type": "application/pdf",
                    "data": base64.standard_b64encode(daten).decode("ascii"),
                },
                "title": datei.name,
            })
        else:
            text = daten.decode("utf-8", errors="replace")
            bloecke.append({
                "type": "document",
                "source": {"type": "text", "media_type": "text/plain", "data": text},
                "title": datei.name,
            })
    if notizen_text.strip():
        bloecke.append({
            "type": "document",
            "source": {"type": "text", "media_type": "text/plain", "data": notizen_text},
            "title": "Eigene Notizen",
        })
    if bloecke:
        # Zwischenspeichern der Unterlagen: Wenn du danach Karteikarten und Quiz
        # erstellst, werden die gleichen Unterlagen günstiger verarbeitet.
        bloecke[-1]["cache_control"] = {"type": "ephemeral"}
    return bloecke


def frage_claude(api_key, modell, bloecke, auftrag, format_klasse=None):
    """Schickt Unterlagen + Auftrag an Claude und gibt die Antwort zurück."""
    client = anthropic.Anthropic(api_key=api_key)
    extra = {}
    if modell in MODELLE_MIT_FALLBACK:
        extra = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
    if format_klasse is not None:
        extra["output_format"] = format_klasse

    antwort = client.beta.messages.parse(
        model=modell,
        max_tokens=MAX_TOKENS,
        system=(
            "Du bist ein geduldiger Lern-Tutor für Studierende. "
            "Antworte immer auf Deutsch. Nutze nur Inhalte aus den Unterlagen. "
            "Wenn etwas in den Unterlagen fehlt, erfinde nichts."
        ),
        messages=[{"role": "user", "content": bloecke + [{"type": "text", "text": auftrag}]}],
        **extra,
    )

    if antwort.stop_reason == "refusal":
        raise RuntimeError("Claude hat diese Anfrage abgelehnt. Versuche es mit anderen Unterlagen.")
    if antwort.stop_reason == "max_tokens":
        raise RuntimeError("Die Antwort wurde zu lang. Wähle weniger Karten/Fragen oder kleinere Unterlagen.")

    if format_klasse is not None:
        return antwort.parsed_output
    return "".join(b.text for b in antwort.content if b.type == "text")


def mit_fehlermeldung(funktion, *args):
    """Führt eine Anfrage aus und zeigt verständliche Fehlermeldungen."""
    try:
        with st.spinner("Claude arbeitet … das kann bei langen Unterlagen 1–2 Minuten dauern."):
            return funktion(*args)
    except anthropic.AuthenticationError:
        st.error("Der API-Schlüssel ist ungültig. Bitte prüfe ihn in der Seitenleiste.")
    except anthropic.PermissionDeniedError:
        st.error("Der API-Schlüssel hat keine Berechtigung für dieses Modell.")
    except anthropic.RateLimitError:
        st.error("Zu viele Anfragen oder Guthaben/Ausgabenlimit erreicht. Bitte kurz warten oder Guthaben prüfen.")
    except anthropic.BadRequestError as e:
        st.error(f"Die Anfrage wurde abgelehnt (z. B. Datei zu groß, max. 32 MB / 600 Seiten): {e.message}")
    except anthropic.APIConnectionError:
        st.error("Keine Verbindung zum Internet oder zu Anthropic.")
    except anthropic.APIStatusError as e:
        st.error(f"Fehler bei Anthropic ({e.status_code}). Bitte später erneut versuchen.")
    except RuntimeError as e:
        st.error(str(e))
    return None


# ---------------------------------------------------------------------------
# Online-Modus: Login und Speichern in Supabase (nur wenn SUPABASE_URL und
# SUPABASE_KEY in den Secrets stehen). Sonst läuft alles lokal wie bisher.
# ---------------------------------------------------------------------------

def online_modus() -> bool:
    try:
        return "SUPABASE_URL" in st.secrets and "SUPABASE_KEY" in st.secrets
    except Exception:
        return False  # keine secrets.toml vorhanden


ONLINE = online_modus()


def supabase_client():
    """Eine eigene Verbindung pro Besucher (damit jeder nur seine Daten sieht)."""
    if "supabase" not in st.session_state:
        from supabase import create_client  # nur im Online-Modus nötig
        st.session_state["supabase"] = create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])
    return st.session_state["supabase"]


def anmeldung() -> None:
    """Zeigt Anmelden/Registrieren, bis man angemeldet ist."""
    if st.session_state.get("user_id"):
        return
    st.markdown(
        '<div class="schritt"><div class="nr">🔐</div><div class="titel">Anmelden</div></div>',
        unsafe_allow_html=True,
    )
    st.caption("Mit deinem Konto werden deine Ordner und Quizze online gespeichert. Nur du siehst sie.")
    tab_login, tab_neu = st.tabs(["Anmelden", "Neues Konto"])
    with tab_login:
        with st.form("login"):
            email = st.text_input("E-Mail")
            passwort = st.text_input("Passwort", type="password")
            if st.form_submit_button("Anmelden", type="primary"):
                try:
                    antwort = supabase_client().auth.sign_in_with_password(
                        {"email": email.strip(), "password": passwort})
                    st.session_state["user_id"] = antwort.user.id
                    st.session_state["user_email"] = antwort.user.email
                    st.rerun()
                except Exception as e:
                    if "confirm" in str(e).lower():
                        st.error("Bitte bestätige zuerst deine E-Mail (Link in deinem Postfach).")
                    else:
                        st.error("Anmelden hat nicht geklappt. Stimmen E-Mail und Passwort?")
    with tab_neu:
        with st.form("registrieren"):
            email = st.text_input("E-Mail", key="neu_email")
            passwort = st.text_input("Passwort (mindestens 6 Zeichen)", type="password", key="neu_pw")
            if st.form_submit_button("Konto erstellen", type="primary"):
                try:
                    antwort = supabase_client().auth.sign_up({"email": email.strip(), "password": passwort})
                    if antwort.session:
                        st.session_state["user_id"] = antwort.user.id
                        st.session_state["user_email"] = antwort.user.email
                        st.rerun()
                    else:
                        st.success("Konto erstellt. Bitte bestätige deine E-Mail und melde dich dann an.")
                except Exception as e:
                    st.error(f"Konto erstellen hat nicht geklappt: {e}")
    st.stop()


def abmelden() -> None:
    try:
        supabase_client().auth.sign_out()
    except Exception:
        pass
    for k in list(st.session_state.keys()):
        del st.session_state[k]
    st.rerun()


# ---------------------------------------------------------------------------
# Speichern: Ordner und Dokumente als Datei auf deinem Computer
# (Ordner "meine_daten" neben app.py, Datei "lern_daten.json")
# ---------------------------------------------------------------------------

DATEN_DATEI = Path(__file__).parent / "meine_daten" / "lern_daten.json"
DOK_SYMBOL = {"quiz": "❓", "karten": "🗂️", "zusammenfassung": "📝"}
DOK_NAME = {"quiz": "Quiz", "karten": "Karteikarten", "zusammenfassung": "Zusammenfassung"}


def daten_laden() -> dict:
    if ONLINE:
        if "online_daten" not in st.session_state:
            antwort = (supabase_client().table("lern_daten").select("daten")
                       .eq("user_id", st.session_state["user_id"]).execute())
            daten = antwort.data[0]["daten"] if antwort.data else {}
            daten.setdefault("ordner", {})
            daten.setdefault("dokumente", {})
            st.session_state["online_daten"] = daten
        return copy.deepcopy(st.session_state["online_daten"])
    try:
        return json.loads(DATEN_DATEI.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {"ordner": {}, "dokumente": {}}


def daten_speichern(daten: dict) -> None:
    if ONLINE:
        (supabase_client().table("lern_daten")
         .upsert({"user_id": st.session_state["user_id"], "daten": daten}, on_conflict="user_id").execute())
        st.session_state["online_daten"] = copy.deepcopy(daten)
        return
    DATEN_DATEI.parent.mkdir(exist_ok=True)
    zwischen = DATEN_DATEI.with_suffix(".tmp")
    zwischen.write_text(json.dumps(daten, ensure_ascii=False, indent=2), encoding="utf-8")
    zwischen.replace(DATEN_DATEI)  # erst fertig schreiben, dann austauschen: so geht nichts kaputt


def dokument_speichern(typ: str, inhalt) -> None:
    """Speichert ein neues Ergebnis im gerade geöffneten Ordner (falls einer gewählt ist)."""
    ordner_id = st.session_state.get("ordner_id")
    if not ordner_id:
        return
    daten = daten_laden()
    if ordner_id not in daten["ordner"]:
        return
    dok_id = uuid.uuid4().hex[:10]
    daten["dokumente"][dok_id] = {
        "name": f"{DOK_NAME[typ]} vom {datetime.now():%d.%m.%Y, %H:%M}",
        "typ": typ,
        "ordner_id": ordner_id,
        "erstellt": datetime.now().isoformat(timespec="seconds"),
        "inhalt": inhalt,
        "ergebnis": None,
    }
    daten_speichern(daten)
    st.session_state[f"dok_{typ}"] = dok_id
    st.toast(f"Gespeichert in „{daten['ordner'][ordner_id]['name']}“", icon="💾")


def quiz_ergebnis_speichern(ergebnisse: dict) -> None:
    dok_id = st.session_state.get("dok_quiz")
    if not dok_id:
        return
    daten = daten_laden()
    if dok_id in daten["dokumente"]:
        daten["dokumente"][dok_id]["ergebnis"] = {str(k): list(v) for k, v in ergebnisse.items()}
        daten_speichern(daten)


def dokument_oeffnen(dok_id: str, dok: dict) -> None:
    typ, inhalt = dok["typ"], dok["inhalt"]
    if typ == "zusammenfassung":
        st.session_state["zusammenfassung"] = inhalt
    elif typ == "karten":
        st.session_state["karten"] = Karteikarten.model_validate(inhalt).karten
        st.session_state["karte_nr"] = 0
        st.session_state["umgedreht"] = False
    elif typ == "quiz":
        st.session_state["quiz"] = Quiz.model_validate(inhalt).fragen
        st.session_state["quiz_runde"] = st.session_state.get("quiz_runde", 0) + 1
        alt = dok.get("ergebnis")
        st.session_state["quiz_ergebnis"] = {int(k): tuple(v) for k, v in alt.items()} if alt else None
    st.session_state[f"dok_{typ}"] = dok_id
    st.toast(f"„{dok['name']}“ geöffnet. Schau im Tab {DOK_NAME[typ]}.", icon="📂")


def ordner_seitenleiste() -> None:
    """Ordner und Dokumente in der Seitenleiste."""
    daten = daten_laden()
    st.divider()
    st.subheader("📁 Meine Ordner")

    with st.form("neuer_ordner", clear_on_submit=True, border=False):
        name = st.text_input("Neuer Ordner", placeholder="z. B. Statistik 1")
        if st.form_submit_button("➕ Ordner anlegen") and name.strip():
            neu_id = uuid.uuid4().hex[:10]
            daten["ordner"][neu_id] = {"name": name.strip()}
            daten_speichern(daten)
            st.session_state["ordner_id"] = neu_id
            st.rerun()

    if not daten["ordner"]:
        st.caption("Noch keine Ordner. Lege einen an, dann werden deine Ergebnisse dort gespeichert.")
        st.session_state["ordner_id"] = None
        return

    ordner_ids = [None] + sorted(daten["ordner"], key=lambda i: daten["ordner"][i]["name"].lower())
    if st.session_state.get("ordner_id") not in ordner_ids:
        st.session_state["ordner_id"] = None

    def ordner_label(i):
        if i is None:
            return "— kein Ordner (nicht speichern) —"
        anzahl = sum(1 for d in daten["dokumente"].values() if d["ordner_id"] == i)
        return f"📁 {daten['ordner'][i]['name']} ({anzahl})"

    ordner_id = st.selectbox("Geöffneter Ordner", ordner_ids, format_func=ordner_label, key="ordner_id")
    if ordner_id is None:
        st.caption("Wähle einen Ordner, damit neue Ergebnisse automatisch gespeichert werden.")
        return

    st.caption("Neue Zusammenfassungen, Karteikarten und Quizze werden hier automatisch gespeichert.")

    dokumente = sorted(
        ((i, d) for i, d in daten["dokumente"].items() if d["ordner_id"] == ordner_id),
        key=lambda x: x[1]["erstellt"], reverse=True,
    )
    if not dokumente:
        st.info("Dieser Ordner ist noch leer.")
    for dok_id, dok in dokumente:
        titel = f"{DOK_SYMBOL[dok['typ']]} {dok['name']}"
        if dok["typ"] == "quiz" and dok.get("ergebnis"):
            punkte = sum(PUNKTE.get(v[0], 0) for v in dok["ergebnis"].values())
            titel += f" · {punkte:g}/{len(dok['inhalt']['fragen'])}".replace(".", ",")
        with st.expander(titel):
            if st.button("📂 Öffnen", key=f"oeffnen_{dok_id}", type="primary", use_container_width=True):
                dokument_oeffnen(dok_id, dok)
            neuer_name = st.text_input("Name", value=dok["name"], key=f"name_{dok_id}")
            if st.button("✏️ Umbenennen", key=f"umbenennen_{dok_id}", use_container_width=True):
                if neuer_name.strip():
                    daten["dokumente"][dok_id]["name"] = neuer_name.strip()
                    daten_speichern(daten)
                    st.rerun()
            andere = [i for i in ordner_ids if i not in (None, ordner_id)]
            if andere:
                ziel = st.selectbox("Verschieben nach", andere,
                                    format_func=lambda i: daten["ordner"][i]["name"], key=f"ziel_{dok_id}")
                if st.button("📦 Verschieben", key=f"verschieben_{dok_id}", use_container_width=True):
                    daten["dokumente"][dok_id]["ordner_id"] = ziel
                    daten_speichern(daten)
                    st.rerun()
            sicher = st.checkbox("Ja, wirklich löschen", key=f"sicher_{dok_id}")
            if st.button("🗑️ Löschen", key=f"loeschen_{dok_id}", disabled=not sicher, use_container_width=True):
                del daten["dokumente"][dok_id]
                daten_speichern(daten)
                st.rerun()

    with st.expander("⚙️ Ordner bearbeiten"):
        ordner_name = st.text_input("Ordnername", value=daten["ordner"][ordner_id]["name"], key=f"oname_{ordner_id}")
        if st.button("✏️ Ordner umbenennen", use_container_width=True) and ordner_name.strip():
            daten["ordner"][ordner_id]["name"] = ordner_name.strip()
            daten_speichern(daten)
            st.rerun()
        sicher = st.checkbox("Ordner mit allen Dokumenten löschen", key=f"osicher_{ordner_id}")
        if st.button("🗑️ Ordner löschen", disabled=not sicher, use_container_width=True):
            daten["dokumente"] = {i: d for i, d in daten["dokumente"].items() if d["ordner_id"] != ordner_id}
            del daten["ordner"][ordner_id]
            daten_speichern(daten)
            st.session_state.pop("ordner_id", None)
            st.rerun()


# ---------------------------------------------------------------------------
# Design: Farben, Animationen und Grafiken (alles selbst erstellt, kein Bild aus dem Internet)
# ---------------------------------------------------------------------------

STIL = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;800&display=swap');
html, body, [class*="css"], .stMarkdown, button, input, textarea { font-family: 'Inter', sans-serif; }

@keyframes einblenden { from { opacity: 0; transform: translateY(14px); } to { opacity: 1; transform: none; } }
@keyframes schweben   { 0%,100% { transform: translateY(0); } 50% { transform: translateY(-8px); } }
@keyframes leuchten   { 0%,100% { opacity: .55; } 50% { opacity: 1; } }
@keyframes umdrehen   { from { transform: perspective(900px) rotateY(90deg); opacity: .2; }
                        to   { transform: perspective(900px) rotateY(0); opacity: 1; } }
@keyframes verlauf    { 0% { background-position: 0% 50%; } 50% { background-position: 100% 50%; } 100% { background-position: 0% 50%; } }

.block-container { padding-top: 3.5rem; max-width: 1100px; }

/* Kopfbereich */
.hero { display: flex; align-items: center; justify-content: space-between; gap: 24px;
        padding: 28px 36px; border-radius: 24px; color: white; margin-bottom: 28px;
        background: linear-gradient(120deg, #6C5CE7, #8E7CF8, #00B8D9, #6C5CE7);
        background-size: 300% 300%; animation: verlauf 14s ease infinite, einblenden .7s ease both;
        box-shadow: 0 18px 40px rgba(108, 92, 231, .25); }
.hero h1 { color: white; font-size: 2.4rem; font-weight: 800; margin: 0; padding: 0; }
.hero p  { color: rgba(255,255,255,.9); font-size: 1.05rem; margin: 6px 0 0 0; }
.hero svg { flex-shrink: 0; animation: schweben 4s ease-in-out infinite; }
.hero .funke { animation: leuchten 2.2s ease-in-out infinite; }
@media (max-width: 700px) { .hero { flex-direction: column; text-align: center; } }

/* Schritt-Überschriften */
.schritt { display: flex; align-items: center; gap: 12px; margin: 26px 0 10px 0; animation: einblenden .6s ease both; }
.schritt .nr { width: 34px; height: 34px; border-radius: 50%; display: grid; place-items: center;
               background: #6C5CE7; color: white; font-weight: 800; box-shadow: 0 6px 14px rgba(108,92,231,.35); }
.schritt .titel { font-size: 1.35rem; font-weight: 800; color: #1E1B3A; }

/* Buttons */
.stButton > button, .stDownloadButton > button, [data-testid="stFormSubmitButton"] > button {
    border-radius: 12px; font-weight: 600; transition: transform .15s ease, box-shadow .15s ease; }
.stButton > button:hover, .stDownloadButton > button:hover, [data-testid="stFormSubmitButton"] > button:hover {
    transform: translateY(-2px); box-shadow: 0 8px 18px rgba(108,92,231,.25); }

/* Upload-Feld und Tabs */
[data-testid="stFileUploaderDropzone"] { border-radius: 16px; border: 2px dashed #B9B1F5; background: white; }
.stTabs [data-baseweb="tab-list"] { gap: 6px; }
.stTabs [data-baseweb="tab"] { border-radius: 12px 12px 0 0; padding: 10px 18px; font-weight: 600; }
[data-testid="stTabContent"], .stTabs [role="tabpanel"] { animation: einblenden .45s ease both; }

/* Hinweise und Ergebnis-Boxen */
[data-testid="stAlert"] { border-radius: 14px; animation: einblenden .45s ease both; }
[data-testid="stMetric"] { background: white; border-radius: 16px; padding: 16px 20px;
                           box-shadow: 0 6px 20px rgba(30,27,58,.06); animation: einblenden .5s ease both; }

/* Seitenleiste */
[data-testid="stSidebar"] { background: linear-gradient(180deg, #EEEDFB 0%, #F7F7FB 100%); }

/* Karteikarte */
.karte { min-height: 230px; border-radius: 22px; padding: 34px 38px; margin: 8px 0 18px 0;
         display: flex; flex-direction: column; justify-content: center; animation: umdrehen .45s ease both;
         box-shadow: 0 16px 36px rgba(30,27,58,.10); }
.karte.vorne  { background: white; border: 2px solid #E3E0FB; }
.karte.hinten { background: linear-gradient(135deg, #6C5CE7, #00B8D9); color: white; }
.karte .etikett { font-size: .8rem; letter-spacing: .12em; text-transform: uppercase; font-weight: 700; opacity: .7; margin-bottom: 10px; }
.karte .inhalt  { font-size: 1.45rem; font-weight: 600; line-height: 1.45; white-space: pre-wrap; }
.karte.hinten .inhalt { font-size: 1.15rem; font-weight: 400; }

/* Fragetyp-Kacheln */
[data-testid="stPills"] button, [data-testid="stButtonGroup"] button { border-radius: 999px; padding: 8px 18px; font-weight: 600; }
.typ-info { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; margin: 6px 0 18px 0; }
.typ-karte { background: white; border: 2px solid #E3E0FB; border-radius: 16px; padding: 14px 16px; opacity: .55;
             transition: all .25s ease; }
.typ-karte.aktiv { opacity: 1; border-color: #6C5CE7; box-shadow: 0 10px 24px rgba(108,92,231,.18); transform: translateY(-2px); }
.typ-symbol { font-size: 1.6rem; }
.typ-titel { font-weight: 800; margin-top: 4px; }
.typ-text { font-size: .88rem; color: #6B6890; margin-top: 2px; }

/* Leerer Zustand */
.leer { text-align: center; padding: 26px 10px 8px 10px; color: #6B6890; animation: einblenden .6s ease both; }
.leer svg { animation: schweben 4s ease-in-out infinite; }
</style>
"""

# Selbst gezeichnete Grafik: Bücherstapel mit Glühbirne
HERO_GRAFIK = """
<svg width="150" height="130" viewBox="0 0 150 130" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
  <ellipse cx="75" cy="122" rx="58" ry="6" fill="rgba(0,0,0,.12)"/>
  <rect x="22" y="92" width="106" height="24" rx="5" fill="#FFD166"/>
  <rect x="22" y="92" width="10" height="24" rx="3" fill="#F4A940"/>
  <rect x="30" y="66" width="92" height="24" rx="5" fill="#FFFFFF"/>
  <rect x="30" y="66" width="10" height="24" rx="3" fill="#DAD5FF"/>
  <rect x="38" y="76" width="70" height="3" rx="1.5" fill="#DAD5FF"/>
  <rect x="18" y="40" width="100" height="24" rx="5" fill="#FF7A90"/>
  <rect x="18" y="40" width="10" height="24" rx="3" fill="#E85D75"/>
  <g transform="translate(112 8)">
    <circle class="funke" cx="14" cy="16" r="22" fill="rgba(255,236,153,.35)"/>
    <path d="M14 2a12 12 0 0 0-7 21.7V28h14v-4.3A12 12 0 0 0 14 2z" fill="#FFE27A"/>
    <rect x="7" y="29" width="14" height="4" rx="2" fill="#FFFFFF"/>
    <rect x="9" y="34" width="10" height="3" rx="1.5" fill="#FFFFFF"/>
  </g>
</svg>
"""

LEER_GRAFIK = """
<svg width="90" height="70" viewBox="0 0 90 70" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
  <rect x="14" y="14" width="58" height="40" rx="8" fill="#DAD5FF"/>
  <rect x="20" y="8" width="58" height="40" rx="8" fill="#FFFFFF" stroke="#B9B1F5" stroke-width="2"/>
  <rect x="30" y="20" width="36" height="4" rx="2" fill="#B9B1F5"/>
  <rect x="30" y="30" width="24" height="4" rx="2" fill="#DAD5FF"/>
</svg>
"""


def kopfbereich():
    st.markdown(STIL, unsafe_allow_html=True)
    st.markdown(
        f"""<div class="hero"><div><h1>Lern-App</h1>
        <p>Lade deine Vorlesung hoch und lerne mit Zusammenfassung, Karteikarten und Quiz.</p></div>
        {HERO_GRAFIK}</div>""",
        unsafe_allow_html=True,
    )


def schritt(nr, titel):
    st.markdown(f'<div class="schritt"><div class="nr">{nr}</div><div class="titel">{titel}</div></div>',
                unsafe_allow_html=True)


def leer_hinweis(text):
    st.markdown(f'<div class="leer">{LEER_GRAFIK}<p>{html.escape(text)}</p></div>', unsafe_allow_html=True)


def karte_anzeigen(text, hinten):
    seite = "hinten" if hinten else "vorne"
    etikett = "Antwort" if hinten else "Frage"
    st.markdown(
        f'<div class="karte {seite}"><div class="etikett">{etikett}</div>'
        f'<div class="inhalt">{html.escape(text)}</div></div>',
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Oberfläche
# ---------------------------------------------------------------------------

st.set_page_config(page_title="Lern-App", page_icon="📚", layout="wide")
kopfbereich()
if ONLINE:
    anmeldung()

# --- Seitenleiste: Schlüssel und Modell ---
with st.sidebar:
    st.header("⚙️ Einstellungen")
    if ONLINE:
        st.caption(f"👤 Angemeldet als {st.session_state.get('user_email', '')}")
        if st.button("Abmelden"):
            abmelden()
    # Online wird nie ein hinterlegter Schlüssel benutzt: jeder gibt seinen eigenen ein.
    vorbelegt = "" if ONLINE else api_schluessel_vorbelegt()
    if vorbelegt:
        st.success("Ein API-Schlüssel ist auf diesem Computer hinterlegt.")
        api_key = vorbelegt
    else:
        api_key = st.text_input(
            "Dein Claude API-Schlüssel",
            type="password",
            help="Beginnt mit 'sk-ant-'. Wird nur für diese Sitzung benutzt und nicht gespeichert.",
        )
        st.caption("Den Schlüssel bekommst du unter console.anthropic.com → API Keys.")

    modell_name = st.selectbox("Modell", list(MODELLE.keys()))
    modell = MODELLE[modell_name]

    ordner_seitenleiste()

# --- Schritt 1: Unterlagen ---
schritt(1, "Unterlagen hochladen")
dateien = st.file_uploader(
    "Vorlesungsfolien und Notizen (PDF, TXT oder MD). PowerPoint bitte vorher als PDF speichern.",
    type=["pdf", "txt", "md"],
    accept_multiple_files=True,
)
notizen = st.text_area("Oder Notizen hier einfügen (optional)", height=120)

bloecke = dateien_zu_bloecken(dateien or [], notizen)
bereit = bool(api_key) and bool(bloecke)
if not api_key:
    st.info("Bitte gib links deinen API-Schlüssel ein.")
elif not bloecke:
    st.info("Bitte lade mindestens eine Datei hoch oder füge Notizen ein.")

# --- Schritt 2: Lernen ---
schritt(2, "Lernen")
tab_zf, tab_karten, tab_quiz = st.tabs(["📝 Zusammenfassung", "🗂️ Karteikarten", "❓ Quiz"])

# Zusammenfassung
with tab_zf:
    if st.button("✨ Zusammenfassung erstellen", disabled=not bereit, type="primary"):
        text = mit_fehlermeldung(
            frage_claude, api_key, modell, bloecke,
            "Erstelle eine klar gegliederte Zusammenfassung der Unterlagen mit "
            "Überschriften und Stichpunkten. Erkläre die wichtigsten Begriffe kurz. "
            "Ende mit einem Abschnitt 'Das Wichtigste in 5 Sätzen'.",
        )
        if text:
            st.session_state["zusammenfassung"] = text
            dokument_speichern("zusammenfassung", text)
    if "zusammenfassung" not in st.session_state:
        leer_hinweis("Noch keine Zusammenfassung. Klick auf den Button oben.")
    else:
        with st.container(border=True):
            st.markdown(st.session_state["zusammenfassung"])
        st.download_button(
            "⬇️ Als Datei herunterladen",
            st.session_state["zusammenfassung"],
            file_name="zusammenfassung.md",
        )

# Karteikarten
with tab_karten:
    anzahl_karten = st.slider("Anzahl Karteikarten", 5, 30, 10)
    if st.button("✨ Karteikarten erstellen", disabled=not bereit, type="primary"):
        ergebnis = mit_fehlermeldung(
            frage_claude, api_key, modell, bloecke,
            f"Erstelle genau {anzahl_karten} Karteikarten zu den wichtigsten Inhalten. "
            "Vorderseite: kurze Frage oder Begriff. Rückseite: knappe, richtige Antwort.",
            Karteikarten,
        )
        if ergebnis and ergebnis.karten:
            st.session_state["karten"] = ergebnis.karten
            st.session_state["karte_nr"] = 0
            st.session_state["umgedreht"] = False
            dokument_speichern("karten", ergebnis.model_dump())

    if not st.session_state.get("karten"):
        leer_hinweis("Noch keine Karteikarten. Wähle die Anzahl und klick auf den Button.")
    else:
        karten = st.session_state["karten"]
        nr = st.session_state["karte_nr"]
        karte = karten[nr]
        st.progress((nr + 1) / len(karten), text=f"Karte {nr + 1} von {len(karten)}")
        if st.session_state["umgedreht"]:
            karte_anzeigen(karte.rueckseite, hinten=True)
        else:
            karte_anzeigen(karte.vorderseite, hinten=False)

        spalte1, spalte2, spalte3 = st.columns(3)
        if spalte1.button("⬅️ Zurück", disabled=nr == 0):
            st.session_state["karte_nr"] -= 1
            st.session_state["umgedreht"] = False
            st.rerun()
        if spalte2.button("🔄 Umdrehen"):
            st.session_state["umgedreht"] = not st.session_state["umgedreht"]
            st.rerun()
        if spalte3.button("Weiter ➡️", disabled=nr >= len(karten) - 1):
            st.session_state["karte_nr"] += 1
            st.session_state["umgedreht"] = False
            st.rerun()

# Quiz
with tab_quiz:
    st.markdown("**Welche Fragetypen möchtest du?** Klick an, was du willst (mehrere möglich).")
    gewaehlte_typen = st.pills(
        "Fragetypen",
        options=list(FRAGETYPEN.keys()),
        selection_mode="multi",
        default=list(FRAGETYPEN.keys()),
        format_func=lambda t: TYP_KACHEL[t],
        label_visibility="collapsed",
        key="fragetypen",
    ) or []
    st.markdown(
        '<div class="typ-info">' + "".join(
            f'<div class="typ-karte{" aktiv" if t in gewaehlte_typen else ""}">'
            f'<div class="typ-symbol">{TYP_SYMBOL[t]}</div><div class="typ-titel">{TYP_TITEL[t]}</div>'
            f'<div class="typ-text">{TYP_TEXT[t]}</div></div>'
            for t in FRAGETYPEN) + "</div>",
        unsafe_allow_html=True,
    )
    anzahl_fragen = st.slider("Anzahl Fragen", 3, 20, 6)
    if not gewaehlte_typen:
        st.info("Bitte wähle mindestens einen Fragetyp.")

    if st.button("✨ Quiz erstellen", disabled=not bereit or not gewaehlte_typen, type="primary"):
        typ_beschreibung = "\n".join(f"- {t}: {FRAGETYPEN[t]}" for t in gewaehlte_typen)
        ergebnis = mit_fehlermeldung(
            frage_claude, api_key, modell, bloecke,
            f"Erstelle ein Quiz mit genau {anzahl_fragen} Fragen zu den Unterlagen. "
            f"Verwende nur diese Fragetypen und verteile die Fragen ungefähr gleichmäßig:\n{typ_beschreibung}\n\n"
            "Regeln:\n"
            "- single: genau 4 Antwortmöglichkeiten, genau eine ist richtig.\n"
            "- multiple: 4 bis 5 Antwortmöglichkeiten, mindestens eine und höchstens alle bis auf eine sind richtig. "
            "Schreibe in die Frage nicht, wie viele richtig sind.\n"
            "- offen: antworten ist eine leere Liste, richtige_antworten ist eine leere Liste. "
            "Stelle Verständnisfragen, die man in 1 bis 4 Sätzen beantworten kann.\n"
            "- richtige_antworten enthält die Nummern der richtigen Antworten (0 = erste). "
            "Mische die Position der richtigen Antworten.\n"
            "- musterloesung: die richtige Antwort in ganzen Sätzen.\n"
            "- erklaerung: kurz, warum das richtig ist.",
            Quiz,
        )
        if ergebnis and ergebnis.fragen:
            st.session_state["quiz"] = ergebnis.fragen
            st.session_state["quiz_ergebnis"] = None
            st.session_state["quiz_runde"] = st.session_state.get("quiz_runde", 0) + 1
            dokument_speichern("quiz", ergebnis.model_dump())

    if not st.session_state.get("quiz"):
        leer_hinweis("Noch kein Quiz. Wähle die Fragetypen und klick auf den Button.")
    else:
        fragen = st.session_state["quiz"]
        runde = st.session_state["quiz_runde"]
        with st.form(f"quiz_formular_{runde}"):
            for i, f in enumerate(fragen):
                st.markdown(f"**{i + 1}. {f.frage}**")
                if f.typ == "single":
                    st.caption("Single Choice: eine Antwort wählen")
                    st.radio(
                        "Antwort", options=list(range(len(f.antworten))),
                        format_func=lambda x, f=f: f.antworten[x],
                        index=None, key=f"q_{runde}_{i}", label_visibility="collapsed",
                    )
                elif f.typ == "multiple":
                    st.caption("Multiple Choice: alle richtigen Antworten ankreuzen")
                    for j, antwort in enumerate(f.antworten):
                        st.checkbox(antwort, key=f"q_{runde}_{i}_{j}")
                else:
                    st.caption("Offene Frage: in eigenen Worten antworten")
                    st.text_area("Deine Antwort", key=f"q_{runde}_{i}", label_visibility="collapsed")
                st.write("")
            abgegeben = st.form_submit_button("Antworten prüfen")

        if abgegeben:
            ergebnisse = {}  # frage_nr -> (ergebnis, feedback)
            offene = []
            for i, f in enumerate(fragen):
                richtige = set(f.richtige_antworten)
                if f.typ == "single":
                    wahl = st.session_state.get(f"q_{runde}_{i}")
                    ergebnisse[i] = ("richtig" if wahl in richtige else "falsch", "")
                elif f.typ == "multiple":
                    wahl = {j for j in range(len(f.antworten)) if st.session_state.get(f"q_{runde}_{i}_{j}")}
                    if wahl == richtige:
                        ergebnisse[i] = ("richtig", "")
                    elif wahl & richtige and not (wahl - richtige):
                        ergebnisse[i] = ("teilweise richtig", "Du hast nicht alle richtigen Antworten angekreuzt.")
                    else:
                        ergebnisse[i] = ("falsch", "")
                else:
                    antwort = (st.session_state.get(f"q_{runde}_{i}") or "").strip()
                    if not antwort:
                        ergebnisse[i] = ("falsch", "Keine Antwort gegeben.")
                    else:
                        offene.append((i, f, antwort))

            # Offene Antworten bewertet Claude (eine Anfrage für alle offenen Fragen).
            if offene:
                auftrag = (
                    "Bewerte die Antworten eines Studierenden auf offene Quizfragen. "
                    "Vergleiche mit der Musterlösung und den Unterlagen. Sei fair: Andere Formulierungen sind ok, "
                    "wenn der Inhalt stimmt. ergebnis ist 'richtig', 'teilweise richtig' oder 'falsch'. "
                    "feedback: 1 bis 3 freundliche Sätze, was gut war und was fehlt. "
                    "frage_nr ist die Nummer aus der Liste.\n\n"
                )
                for i, f, antwort in offene:
                    auftrag += (
                        f"Frage {i}: {f.frage}\nMusterlösung: {f.musterloesung}\n"
                        f"Antwort des Studierenden: {antwort}\n\n"
                    )
                bewertet = mit_fehlermeldung(frage_claude, api_key, modell, bloecke, auftrag, Bewertungen)
                if bewertet:
                    for b in bewertet.bewertungen:
                        ergebnisse[b.frage_nr] = (b.ergebnis, b.feedback)
                for i, _, _ in offene:
                    ergebnisse.setdefault(i, ("falsch", "Diese Antwort konnte nicht bewertet werden."))
            st.session_state["quiz_ergebnis"] = ergebnisse
            quiz_ergebnis_speichern(ergebnisse)

        ergebnisse = st.session_state.get("quiz_ergebnis")
        if ergebnisse:
            st.subheader("Auswertung")
            punkte = 0.0
            for i, f in enumerate(fragen):
                ergebnis, feedback = ergebnisse.get(i, ("falsch", ""))
                punkte += PUNKTE.get(ergebnis, 0.0)
                if f.typ == "offen":
                    loesung = f.musterloesung
                else:
                    loesung = ", ".join(f.antworten[j] for j in f.richtige_antworten if 0 <= j < len(f.antworten))
                text = f"**Frage {i + 1}: {ergebnis}.** {feedback}\n\n**Lösung:** {loesung}\n\n{f.erklaerung}"
                if ergebnis == "richtig":
                    st.success(text)
                elif ergebnis == "teilweise richtig":
                    st.warning(text)
                else:
                    st.error(text)
            punkte_text = f"{punkte:g}".replace(".", ",")
            st.metric("Ergebnis", f"{punkte_text} von {len(fragen)} Punkten")
            st.caption("Richtig = 1 Punkt, teilweise richtig = ½ Punkt.")
            if punkte == len(fragen):
                st.balloons()
