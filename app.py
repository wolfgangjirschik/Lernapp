"""Uniwolf – Lern-App (Probeversion)

Lade Vorlesungsfolien (PDF) und eigene Notizen (PDF, TXT, MD) hoch.
Claude erstellt daraus eine Zusammenfassung, Karteikarten und ein Quiz.

Start:  streamlit run app.py
"""

import base64
import copy
import html
import json
import os
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Literal

import anthropic
import streamlit as st
from streamlit.errors import StreamlitAPIException
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

# Preise in US-Dollar pro 1 Million Tokens: (Eingabe, Ausgabe, Cache lesen).
# Quelle: https://platform.claude.com/docs/en/about-claude/pricing (Stand Nov. 2026).
# Cache schreiben kostet das 1,25-Fache der Eingabe (5 Minuten Cache).
# Die älteren Modelle stehen hier, weil Claude bei einer Ablehnung automatisch auf sie ausweichen kann.
PREISE_USD = {
    "claude-opus-5-5": (4.00, 20.00, 0.20),
    "claude-sonnet-5-5": (2.00, 10.00, 0.10),
    "claude-haiku-5-5": (0.10, 0.50, 0.01),
    "claude-opus-5": (5.00, 25.00, 0.50),
    "claude-opus-4-8": (5.00, 25.00, 0.50),
    "claude-sonnet-5": (2.00, 10.00, 0.20),
}
HAIKU_LANG_USD = (0.50, 2.50, 0.05)  # Haiku 5.5 bei Anfragen über 100.000 Tokens

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


def frage_claude(api_key, modell, bloecke, auftrag, format_klasse=None, anzeige=None):
    """Schickt Unterlagen + Auftrag an Claude und gibt die Antwort zurück.

    Die Anfrage läuft im Hintergrund. Währenddessen zeigt `anzeige` die Zeit an.
    Klickt man auf „Abbrechen“, startet Streamlit die Seite neu: Dann wird die
    Verbindung zu Claude geschlossen und das halbe Ergebnis verworfen.
    """
    zahlt_mit_guthaben = guthaben_pflicht(api_key)
    if zahlt_mit_guthaben:
        stand = guthaben_stand()
        if stand is not None and stand <= 0:
            raise RuntimeError("Dein Guthaben ist aufgebraucht. Lade es links unter „Guthaben aufladen“ auf.")
    client = anthropic.Anthropic(api_key=api_key)
    extra = {}
    if modell in MODELLE_MIT_FALLBACK:
        extra = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
    if format_klasse is not None:
        extra["output_format"] = format_klasse

    anfrage = dict(
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
    ablage = {}

    def im_hintergrund():
        try:
            with client.beta.messages.stream(**anfrage) as stream:
                ablage["stream"] = stream
                for _ in stream:
                    if ablage.get("stopp"):
                        return
                    ablage["zeichen"] = ablage.get("zeichen", 0) + 1
                ablage["antwort"] = stream.get_final_message()
        except Exception as fehler:  # wird unten im Hauptprogramm weitergegeben
            ablage["fehler"] = fehler

    faden = threading.Thread(target=im_hintergrund, daemon=True)
    faden.start()
    start = time.time()
    try:
        while faden.is_alive():
            faden.join(0.3)
            if anzeige is not None:
                # Jede Anzeige-Änderung gibt Streamlit die Chance, auf „Abbrechen“ zu reagieren.
                schritte = ablage.get("zeichen", 0)
                text = "liest die Unterlagen" if schritte == 0 else "schreibt"
                anzeige.caption(f"⏳ Claude {text} … {time.time() - start:.0f} Sekunden")
    except BaseException:
        ablage["stopp"] = True
        stream = ablage.get("stream")
        if stream is not None:
            stream.close()  # Verbindung schließen: Claude hört auf, nichts wird gespeichert
        raise
    if "fehler" in ablage:
        raise ablage["fehler"]
    antwort = ablage["antwort"]
    if zahlt_mit_guthaben:
        guthaben_abbuchen(kosten_cent(modell, antwort), auftrag[:60])

    if antwort.stop_reason == "refusal":
        raise RuntimeError("Claude hat diese Anfrage abgelehnt. Versuche es mit anderen Unterlagen.")
    if antwort.stop_reason == "max_tokens":
        raise RuntimeError("Die Antwort wurde zu lang. Wähle weniger Karten/Fragen oder kleinere Unterlagen.")

    if format_klasse is not None:
        return antwort.parsed_output
    return "".join(b.text for b in antwort.content if b.type == "text")


def mit_fehlermeldung(funktion, *args):
    """Führt eine Anfrage aus (mit Abbrechen-Knopf) und zeigt verständliche Fehlermeldungen."""
    bereich = st.empty()
    try:
        with bereich.container(border=True, key="arbeitet"):
            st.markdown("**🐺 Claude arbeitet …** Bei langen Unterlagen kann das 1–2 Minuten dauern.")
            anzeige = st.empty()
            st.button("⏹️ Abbrechen", key="abbrechen", on_click=meldung,
                      args=("Abgebrochen. Es wurde nichts erstellt oder gespeichert.", "⏹️"))
        try:
            return funktion(*args, anzeige=anzeige)
        finally:
            bereich.empty()
    except anthropic.AuthenticationError:
        st.error("Der API-Schlüssel ist ungültig. Bitte prüfe ihn in der Seitenleiste.")
    except anthropic.PermissionDeniedError:
        st.error("Der API-Schlüssel hat keine Berechtigung für dieses Modell.")
    except anthropic.RateLimitError:
        st.error("Zu viele Anfragen oder Guthaben/Ausgabenlimit erreicht. Bitte kurz warten oder Guthaben prüfen.")
    except anthropic.BadRequestError as e:
        if "usage limits" in str(e.message).lower():
            st.error("Das monatliche Ausgabenlimit ist erreicht. Bitte später wieder versuchen.")
            return None
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


def email_erlaubt(email: str) -> bool:
    """Optional: Wenn ERLAUBTE_EMAILS in den Secrets steht, dürfen nur diese Adressen rein."""
    try:
        erlaubt = st.secrets.get("ERLAUBTE_EMAILS")
    except Exception:
        erlaubt = None
    if not erlaubt:
        return True
    return email.strip().lower() in {e.strip().lower() for e in erlaubt}


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
                if not email_erlaubt(email):
                    st.error("Diese E-Mail ist für die App nicht freigeschaltet. Frag die Person, die dir den Link geschickt hat.")
                    st.stop()
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
                if not email_erlaubt(email):
                    st.error("Diese E-Mail ist für die App nicht freigeschaltet. Frag die Person, die dir den Link geschickt hat.")
                    st.stop()
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
# Guthaben (nur online, wenn alle mit deinem Claude-Schlüssel arbeiten).
# Der Kontostand liegt in Supabase. Nutzer können ihn nur lesen, nicht ändern:
# Abbuchen und Aufladen laufen über Funktionen in der Datenbank
# (siehe supabase_guthaben.sql). Nur Admins (Tabelle guthaben_admins) können aufladen.
# ---------------------------------------------------------------------------

def geheimnis(name: str, standard=None):
    try:
        return st.secrets.get(name, standard)
    except Exception:
        return standard  # keine secrets.toml vorhanden


def euro(cent: float) -> str:
    return f"{cent / 100:.2f} €".replace(".", ",")


def guthaben_pflicht(api_key: str) -> bool:
    """Guthaben zählt nur online und nur, wenn dein Schlüssel aus den Secrets benutzt wird."""
    return (ONLINE and bool(api_key) and api_key == api_schluessel_vorbelegt()
            and not st.session_state.get("guthaben_fehlt"))


def guthaben_stand():
    """Kontostand in Cent. Beim allerersten Mal schreibt die Datenbank das Startguthaben gut."""
    try:
        stand = float(supabase_client().rpc("guthaben_stand").execute().data)
    except Exception as fehler:
        if "guthaben_stand" in str(fehler) and ("PGRST202" in str(fehler) or "Could not find" in str(fehler)):
            st.session_state["guthaben_fehlt"] = True  # SQL noch nicht ausgeführt: Guthaben aus
            return None
        raise RuntimeError("Dein Guthaben konnte gerade nicht geladen werden. Bitte versuche es gleich nochmal.")
    st.session_state["guthaben_cent"] = stand
    return stand


def kosten_cent(modell: str, antwort) -> float:
    """Echte Kosten einer Anfrage aus den verbrauchten Tokens, in Euro-Cent."""
    u = antwort.usage
    neu = u.input_tokens or 0
    schreiben = getattr(u, "cache_creation_input_tokens", 0) or 0
    lesen = getattr(u, "cache_read_input_tokens", 0) or 0
    aus = u.output_tokens or 0
    benutzt = getattr(antwort, "model", None) or modell  # bei Ausweich-Modell dessen Preis
    preis = PREISE_USD.get(benutzt, PREISE_USD.get(modell, PREISE_USD["claude-opus-5-5"]))
    if benutzt == "claude-haiku-5-5" and neu + schreiben + lesen > 100_000:
        preis = HAIKU_LANG_USD
    ein_p, aus_p, lese_p = preis
    usd = (neu * ein_p + schreiben * ein_p * 1.25 + lesen * lese_p + aus * aus_p) / 1_000_000
    kurs = float(geheimnis("EURO_PRO_DOLLAR", 0.90))
    aufschlag = 1 + float(geheimnis("AUFSCHLAG_PROZENT", 20)) / 100
    return round(usd * kurs * aufschlag * 100, 4)


def guthaben_abbuchen(cent: float, grund: str) -> None:
    try:
        stand = supabase_client().rpc(
            "guthaben_abbuchen", {"kosten_cent": cent, "grund": grund}).execute().data
        st.session_state["guthaben_cent"] = float(stand)
        meldung(f"Kosten: {euro(cent)} · Guthaben: {euro(float(stand))}", "💰")
    except Exception:
        pass  # Ergebnis trotzdem zeigen; der Fehler liegt dann bei der Datenbank


def guthaben_seitenleiste() -> None:
    """Kontostand, Aufladen und (für Admins) Verwaltung."""
    stand = st.session_state.get("guthaben_cent")
    if stand is None or time.time() - st.session_state.get("guthaben_zeit", 0) > 60:
        st.session_state["guthaben_zeit"] = time.time()  # höchstens einmal pro Minute neu laden
        try:
            stand = guthaben_stand()
        except RuntimeError as fehler:
            st.warning(str(fehler))
            return
    if stand is None:  # Guthaben-System noch nicht eingerichtet
        if ist_admin_anzeige():
            st.info("Guthaben ist aus: Bitte supabase_guthaben.sql im Supabase SQL Editor ausführen.")
        return
    st.metric("💰 Dein Guthaben", euro(stand))
    if stand <= 0:
        st.error("Dein Guthaben ist aufgebraucht. Lade es auf, um weiterzulernen.")
    with st.popover("➕ Guthaben aufladen", use_container_width=True):
        email = st.session_state.get("user_email", "")
        st.markdown(
            "1. Klick unten und schick den Betrag, den du aufladen willst.\n"
            f"2. Schreib als **Nachricht/Verwendungszweck** deine E-Mail: `{email}`\n"
            "3. Sobald die Zahlung da ist, wird sie dir gutgeschrieben (nicht automatisch, meist am selben Tag)."
        )
        link = geheimnis("REVOLUT_LINK", "")
        if str(link).startswith("https://"):
            st.link_button("💳 Bezahlen (Revolut, Karte, Apple Pay)", link, use_container_width=True)
        else:
            st.caption("Frag die Person, die dir den Link geschickt hat, wie du zahlen kannst.")
    if ist_admin_anzeige():
        guthaben_verwaltung()


def ist_admin_anzeige() -> bool:
    """Zeigt nur die Verwaltung an. Ob jemand wirklich aufladen darf, prüft die Datenbank selbst."""
    admin = str(geheimnis("ADMIN_EMAIL", "")).strip().lower()
    return bool(admin) and st.session_state.get("user_email", "").lower() == admin


def guthaben_verwaltung() -> None:
    with st.popover("🛠️ Guthaben verwalten", use_container_width=True):
        try:
            liste = supabase_client().rpc("guthaben_liste").execute().data or []
        except Exception:
            st.error("Kein Zugriff. Steht deine E-Mail in der Tabelle guthaben_admins?")
            return
        if liste:
            st.dataframe(
                [{"E-Mail": z["email"], "Guthaben": euro(float(z["cent"]))} for z in liste],
                hide_index=True, use_container_width=True,
            )
        with st.form("aufladen", clear_on_submit=True, border=False):
            ziel = st.selectbox("Person", [z["email"] for z in liste]) if liste else None
            betrag = st.number_input("Betrag in Euro (minus zum Abziehen)", value=5.0, step=1.0, format="%.2f")
            if st.form_submit_button("💰 Gutschreiben", type="primary") and ziel and betrag:
                try:
                    neu = supabase_client().rpc(
                        "guthaben_aufladen", {"ziel_email": ziel, "betrag_cent": round(betrag * 100, 2)}).execute().data
                    meldung(f"{ziel}: neues Guthaben {euro(float(neu))}", "✅")
                    st.rerun()
                except Exception as fehler:
                    st.error(f"Hat nicht geklappt: {fehler}")


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


def meldung(text: str, symbol: str) -> None:
    """Merkt sich eine kurze Meldung; sie erscheint nach dem nächsten Neuladen (st.toast)."""
    st.session_state["meldung"] = (text, symbol)


def meldung_zeigen() -> None:
    if st.session_state.get("meldung"):
        text, symbol = st.session_state.pop("meldung")
        st.toast(text, icon=symbol)


def dokument_speichern(typ: str, inhalt, ordner_id=None, ergebnis=None) -> None:
    """Speichert ein Ergebnis im gewählten Ordner, sonst im gerade geöffneten Ordner (falls einer offen ist)."""
    st.session_state[f"dok_{typ}"] = None  # neues Ergebnis: noch in keinem Ordner
    ordner_id = ordner_id or st.session_state.get("ordner_id")
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
        "ergebnis": ergebnis,
    }
    daten_speichern(daten)
    st.session_state[f"dok_{typ}"] = dok_id
    meldung(f"Gespeichert in „{daten['ordner'][ordner_id]['name']}“", "💾")


NEUER_ORDNER = "__neu__"


def in_ordner_speichern(typ: str) -> None:
    """Zeigt unter einem Ergebnis, in welchem Ordner es liegt, oder einen Knopf zum Speichern."""
    daten = daten_laden()
    dok_id = st.session_state.get(f"dok_{typ}")
    if dok_id in daten["dokumente"]:
        ordner = daten["ordner"].get(daten["dokumente"][dok_id]["ordner_id"], {}).get("name", "?")
        st.caption(f"💾 Gespeichert im Ordner „{ordner}“")
        return
    with st.popover("📁 In Ordner speichern"):
        ordner_ids = sorted(daten["ordner"], key=lambda i: daten["ordner"][i]["name"].lower())
        wahl = st.selectbox(
            "Ordner", ordner_ids + [NEUER_ORDNER], key=f"ziel_ordner_{typ}",
            format_func=lambda i: "➕ Neuer Ordner …" if i == NEUER_ORDNER else daten["ordner"][i]["name"],
        )
        neuer_name = ""
        if wahl == NEUER_ORDNER:
            neuer_name = st.text_input("Name des neuen Ordners", key=f"neuer_ordner_{typ}",
                                       placeholder="z. B. Statistik 1")
        if st.button("💾 Speichern", key=f"speichern_{typ}", type="primary"):
            if wahl == NEUER_ORDNER and not neuer_name.strip():
                st.warning("Bitte gib dem neuen Ordner einen Namen.")
                return
            if wahl == NEUER_ORDNER:
                wahl = uuid.uuid4().hex[:10]
                daten["ordner"][wahl] = {"name": neuer_name.strip()}
                daten_speichern(daten)
            if typ == "zusammenfassung":
                inhalt = st.session_state["zusammenfassung"]
            elif typ == "karten":
                inhalt = {"karten": [k.model_dump() for k in st.session_state["karten"]]}
            else:
                inhalt = {"fragen": [f.model_dump() for f in st.session_state["quiz"]]}
            alt = st.session_state.get("quiz_ergebnis") if typ == "quiz" else None
            ergebnis = {str(k): list(v) for k, v in alt.items()} if alt else None
            dokument_speichern(typ, inhalt, ordner_id=wahl, ergebnis=ergebnis)
            st.rerun()


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
    meldung(f"„{dok['name']}“ geöffnet. Schau im Tab {DOK_NAME[typ]}.", "📂")


def bereich_neu_laden() -> None:
    """Lädt nur die Seitenleiste neu (schneller, ruhiger). Geht das nicht, die ganze Seite."""
    try:
        st.rerun(scope="fragment")
    except StreamlitAPIException:
        st.rerun()


def ordner_umschalten(i: str) -> None:
    st.session_state["ordner_id"] = None if st.session_state.get("ordner_id") == i else i


@st.fragment
def ordner_seitenleiste() -> None:
    """Ordner und Dokumente in der Seitenleiste: Ordner als Kacheln, Dokumente als Karten."""
    daten = daten_laden()
    st.divider()
    st.markdown('<div class="seiten-titel">📁 Meine Ordner</div>', unsafe_allow_html=True)

    ordner_ids = sorted(daten["ordner"], key=lambda i: daten["ordner"][i]["name"].lower())
    if st.session_state.get("ordner_id") not in ordner_ids:
        st.session_state["ordner_id"] = None
    ordner_id = st.session_state["ordner_id"]

    def anzahl(i):
        return sum(1 for d in daten["dokumente"].values() if d["ordner_id"] == i)

    # Ordner-Kacheln: Klick öffnet den Ordner, nochmal Klick schließt ihn
    for i in ordner_ids:
        aktiv = i == ordner_id
        symbol = "📂" if aktiv else "📁"
        st.button(f"{symbol}  {daten['ordner'][i]['name']}  ·  {anzahl(i)}", key=f"ordner_btn_{i}",
                  type="primary" if aktiv else "secondary", use_container_width=True,
                  on_click=ordner_umschalten, args=(i,))

    with st.popover("➕ Neuer Ordner", use_container_width=True):
        with st.form("neuer_ordner", clear_on_submit=True, border=False):
            name = st.text_input("Name des Ordners", placeholder="z. B. Statistik 1")
            if st.form_submit_button("Anlegen", type="primary") and name.strip():
                neu_id = uuid.uuid4().hex[:10]
                daten["ordner"][neu_id] = {"name": name.strip()}
                daten_speichern(daten)
                st.session_state["ordner_id"] = neu_id
                bereich_neu_laden()

    if not ordner_ids:
        st.caption("Noch keine Ordner. Lege einen an, dann werden deine Ergebnisse dort gespeichert.")
        return
    if ordner_id is None:
        st.caption("Klick auf einen Ordner, um ihn zu öffnen. Neue Ergebnisse werden dann dort gespeichert.")
        return

    # Geöffneter Ordner
    st.markdown(
        f'<div class="ordner-kopf">📂 {html.escape(daten["ordner"][ordner_id]["name"])}'
        f'<span>Neues wird hier automatisch gespeichert</span></div>',
        unsafe_allow_html=True,
    )

    dokumente = sorted(
        ((i, d) for i, d in daten["dokumente"].items() if d["ordner_id"] == ordner_id),
        key=lambda x: x[1]["erstellt"], reverse=True,
    )
    if not dokumente:
        st.caption("Dieser Ordner ist noch leer.")

    for dok_id, dok in dokumente:
        datum = datetime.fromisoformat(dok["erstellt"]).strftime("%d.%m.%Y")
        info = f"{DOK_NAME[dok['typ']]} · {datum}"
        if dok["typ"] == "quiz" and dok.get("ergebnis"):
            punkte = sum(PUNKTE.get(v[0], 0) for v in dok["ergebnis"].values())
            info += f" · ⭐ {punkte:g}/{len(dok['inhalt']['fragen'])}".replace(".", ",")
        with st.container(border=True):
            st.markdown(
                f'<div class="dok-karte"><div class="dok-symbol">{DOK_SYMBOL[dok["typ"]]}</div>'
                f'<div><div class="dok-name">{html.escape(dok["name"])}</div>'
                f'<div class="dok-info">{info}</div></div></div>',
                unsafe_allow_html=True,
            )
            links, rechts = st.columns([3, 1])
            if links.button("Öffnen", key=f"oeffnen_{dok_id}", type="primary", use_container_width=True):
                dokument_oeffnen(dok_id, dok)
                st.rerun()  # ganze Seite, damit das Dokument im Tab erscheint
            with rechts.popover("✏️", use_container_width=True):
                neuer_name = st.text_input("Name", value=dok["name"], key=f"name_{dok_id}")
                if st.button("✏️ Umbenennen", key=f"umbenennen_{dok_id}", use_container_width=True):
                    if neuer_name.strip():
                        daten["dokumente"][dok_id]["name"] = neuer_name.strip()
                        daten_speichern(daten)
                        bereich_neu_laden()
                andere = [i for i in ordner_ids if i != ordner_id]
                if andere:
                    ziel = st.selectbox("Verschieben nach", andere,
                                        format_func=lambda i: daten["ordner"][i]["name"], key=f"ziel_{dok_id}")
                    if st.button("📦 Verschieben", key=f"verschieben_{dok_id}", use_container_width=True):
                        daten["dokumente"][dok_id]["ordner_id"] = ziel
                        daten_speichern(daten)
                        bereich_neu_laden()
                sicher = st.checkbox("Ja, wirklich löschen", key=f"sicher_{dok_id}")
                if st.button("🗑️ Löschen", key=f"loeschen_{dok_id}", disabled=not sicher, use_container_width=True):
                    del daten["dokumente"][dok_id]
                    daten_speichern(daten)
                    bereich_neu_laden()

    with st.popover("⚙️ Ordner bearbeiten", use_container_width=True):
        ordner_name = st.text_input("Ordnername", value=daten["ordner"][ordner_id]["name"], key=f"oname_{ordner_id}")
        if st.button("✏️ Ordner umbenennen", use_container_width=True) and ordner_name.strip():
            daten["ordner"][ordner_id]["name"] = ordner_name.strip()
            daten_speichern(daten)
            bereich_neu_laden()
        sicher = st.checkbox("Ordner mit allen Dokumenten löschen", key=f"osicher_{ordner_id}")
        if st.button("🗑️ Ordner löschen", disabled=not sicher, use_container_width=True):
            daten["dokumente"] = {i: d for i, d in daten["dokumente"].items() if d["ordner_id"] != ordner_id}
            del daten["ordner"][ordner_id]
            daten_speichern(daten)
            st.session_state["ordner_id"] = None
            bereich_neu_laden()


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
@keyframes wackeln    { 0%,100% { transform: rotate(0); } 50% { transform: rotate(-4deg); } }
@keyframes verlauf    { 0% { background-position: 0% 50%; } 50% { background-position: 100% 50%; } 100% { background-position: 0% 50%; } }

.block-container { padding-top: 3.5rem; max-width: 1100px; }

/* Kopfbereich */
.hero { display: flex; align-items: center; justify-content: space-between; gap: 24px;
        padding: 28px 36px; border-radius: 24px; color: white; margin-bottom: 28px;
        background: linear-gradient(120deg, #1F4D3A, #2F6B4F, #8B5E3C, #1F4D3A);
        background-size: 300% 300%; animation: verlauf 14s ease infinite, einblenden .7s ease both;
        box-shadow: 0 18px 40px rgba(31,77,58, .25); }
.hero h1 { color: white; font-size: 2.4rem; font-weight: 800; margin: 0; padding: 0; }
.hero p  { color: rgba(255,255,255,.9); font-size: 1.05rem; margin: 6px 0 0 0; }
.hero svg { flex-shrink: 0; animation: schweben 4s ease-in-out infinite; }
.hero .hut { transform-origin: 70px 32px; animation: wackeln 3s ease-in-out infinite; }
@media (max-width: 700px) { .hero { flex-direction: column; text-align: center; } }

/* Schritt-Überschriften */
.schritt { display: flex; align-items: center; gap: 12px; margin: 26px 0 10px 0; animation: einblenden .6s ease both; }
.schritt .nr { width: 34px; height: 34px; border-radius: 50%; display: grid; place-items: center;
               background: #1F4D3A; color: white; font-weight: 800; box-shadow: 0 6px 14px rgba(31,77,58,.35); }
.schritt .titel { font-size: 1.35rem; font-weight: 800; color: #1F2A24; }

/* Buttons */
.stButton > button, .stDownloadButton > button, [data-testid="stFormSubmitButton"] > button {
    border-radius: 12px; font-weight: 600; transition: transform .15s ease, box-shadow .15s ease; }
/* Sanfte Klicks: kurzes Eindrücken, kein Ausgrauen beim Neuladen */
.stButton > button:active { transform: scale(.97) !important; transition: transform .08s ease; }
[data-stale="true"] { opacity: 1 !important; transition: none !important; }
[data-testid="stSidebar"] .stButton > button { transition: background-color .25s ease, color .25s ease,
    box-shadow .25s ease, transform .15s ease; }
.stButton > button:hover, .stDownloadButton > button:hover, [data-testid="stFormSubmitButton"] > button:hover {
    transform: translateY(-2px); box-shadow: 0 8px 18px rgba(31,77,58,.25); }

/* Upload-Feld und Tabs */
[data-testid="stFileUploaderDropzone"] { border-radius: 16px; border: 2px dashed #B89B74; background: white; }
.stTabs [data-baseweb="tab-list"] { gap: 6px; }
.stTabs [data-baseweb="tab"] { border-radius: 12px 12px 0 0; padding: 10px 18px; font-weight: 600; }
[data-testid="stTabContent"], .stTabs [role="tabpanel"] { animation: einblenden .45s ease both; }

/* Hinweise und Ergebnis-Boxen */
[data-testid="stAlert"] { border-radius: 14px; animation: einblenden .45s ease both; }
[data-testid="stMetric"] { background: white; border-radius: 16px; padding: 16px 20px;
                           box-shadow: 0 6px 20px rgba(31,42,36,.06); animation: einblenden .5s ease both; }

/* Seitenleiste */
[data-testid="stSidebar"] { background: linear-gradient(180deg, #EFE8DC 0%, #FAF7F2 100%); }

/* Karteikarte */
.karte { min-height: 230px; border-radius: 22px; padding: 34px 38px; margin: 8px 0 18px 0;
         display: flex; flex-direction: column; justify-content: center; animation: umdrehen .45s ease both;
         box-shadow: 0 16px 36px rgba(31,42,36,.10); }
.karte.vorne  { background: white; border: 2px solid #E4DACA; }
.karte.hinten { background: linear-gradient(135deg, #1F4D3A, #8B5E3C); color: white; }
.karte .etikett { font-size: .8rem; letter-spacing: .12em; text-transform: uppercase; font-weight: 700; opacity: .7; margin-bottom: 10px; }
.karte .inhalt  { font-size: 1.45rem; font-weight: 600; line-height: 1.45; white-space: pre-wrap; }
.karte.hinten .inhalt { font-size: 1.15rem; font-weight: 400; }

/* Fragetyp-Kacheln als Knöpfe */
.st-key-typ_kacheln button { min-height: 130px; border-radius: 18px; padding: 16px; white-space: normal;
    border: 2px solid #E4DACA; transition: all .2s ease; }
.st-key-typ_kacheln button p { font-size: .9rem; line-height: 1.45; margin: 0; white-space: normal !important; overflow: visible !important; text-overflow: clip !important; }
.st-key-typ_kacheln button div { white-space: normal !important; overflow: visible !important; text-overflow: clip !important; max-width: 100%; }
.st-key-typ_kacheln button[kind="primary"] { box-shadow: 0 12px 26px rgba(31,77,58,.28); transform: translateY(-2px); }
.st-key-typ_kacheln button[kind="secondary"] { background: white; opacity: .75; }
/* (alt) Fragetyp-Info */
[data-testid="stPills"] button, [data-testid="stButtonGroup"] button { border-radius: 999px; padding: 8px 18px; font-weight: 600; }
.typ-info { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; margin: 6px 0 18px 0; }
.typ-karte { background: white; border: 2px solid #E4DACA; border-radius: 16px; padding: 14px 16px; opacity: .55;
             transition: all .25s ease; }
.typ-karte.aktiv { opacity: 1; border-color: #1F4D3A; box-shadow: 0 10px 24px rgba(31,77,58,.18); transform: translateY(-2px); }
.typ-symbol { font-size: 1.6rem; }
.typ-titel { font-weight: 800; margin-top: 4px; }
.typ-text { font-size: .88rem; color: #6B5F52; margin-top: 2px; }

/* Ordner in der Seitenleiste */
.seiten-titel { font-size: 1.25rem; font-weight: 800; margin-bottom: 10px; }
[data-testid="stSidebar"] .stButton > button { justify-content: flex-start; text-align: left; border-radius: 14px; padding: 10px 14px; }
[data-testid="stSidebar"] .stButton > button[kind="primary"] { box-shadow: 0 8px 18px rgba(31,77,58,.25); }
.ordner-kopf { margin: 18px 0 10px 0; padding: 12px 14px; border-radius: 14px; font-weight: 800;
               background: linear-gradient(135deg, #1F4D3A, #8B5E3C); color: white; animation: einblenden .4s ease both; }
.ordner-kopf span { display: block; font-weight: 400; font-size: .78rem; opacity: .85; margin-top: 2px; }
.dok-karte { display: flex; gap: 10px; align-items: center; margin-bottom: 4px; }
.dok-symbol { font-size: 1.5rem; width: 40px; height: 40px; display: grid; place-items: center;
              border-radius: 12px; background: #EFE8DC; flex-shrink: 0; }
.dok-name { font-weight: 700; line-height: 1.25; word-break: break-word; }
.dok-info { font-size: .78rem; color: #6B5F52; margin-top: 2px; }
[data-testid="stSidebar"] [data-testid="stVerticalBlockBorderWrapper"] { background: white; border-radius: 16px; animation: einblenden .4s ease both; }

/* Leerer Zustand */
.leer { text-align: center; padding: 26px 10px 8px 10px; color: #6B5F52; animation: einblenden .6s ease both; }
.leer svg { animation: schweben 4s ease-in-out infinite; }
</style>
"""

# Selbst gezeichnetes Logo: Wolfskopf mit Studentenhut (Uniwolf)
HERO_GRAFIK = """
<svg width="150" height="150" viewBox="0 0 140 140" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
  <ellipse cx="70" cy="134" rx="44" ry="5" fill="rgba(0,0,0,.15)"/>
  <!-- Ohren -->
  <polygon points="26,14 54,56 18,70" fill="#5A6570"/>
  <polygon points="114,14 86,56 122,70" fill="#5A6570"/>
  <polygon points="29,26 47,55 25,63" fill="#8B5E3C"/>
  <polygon points="111,26 93,55 115,63" fill="#8B5E3C"/>
  <!-- Kopf -->
  <polygon points="18,66 50,48 70,52 90,48 122,66 112,94 88,114 70,128 52,114 28,94" fill="#7D8893"/>
  <polygon points="18,66 50,48 70,52 52,80 28,94" fill="#6B7681"/>
  <polygon points="122,66 90,48 70,52 88,80 112,94" fill="#8C97A1"/>
  <!-- helles Gesicht -->
  <polygon points="50,74 70,64 90,74 86,100 70,120 54,100" fill="#E9E3D8"/>
  <polygon points="28,94 50,86 54,100 52,114" fill="#E9E3D8"/>
  <polygon points="112,94 90,86 86,100 88,114" fill="#E9E3D8"/>
  <!-- Augen -->
  <polygon points="44,74 60,72 54,80" fill="#F2C14E"/>
  <polygon points="96,74 80,72 86,80" fill="#F2C14E"/>
  <circle cx="54" cy="76" r="2.2" fill="#1F2A24"/>
  <circle cx="86" cy="76" r="2.2" fill="#1F2A24"/>
  <!-- Nase -->
  <path d="M62 104 h16 l-8 9 z" fill="#1F2A24"/>
  <!-- Studentenhut -->
  <g class="hut">
    <polygon points="70,20 104,32 70,44 36,32" fill="#1F4D3A"/>
    <path d="M52 38 v9 q18 9 36 0 v-9 l-18 6 z" fill="#163829"/>
    <line x1="104" y1="32" x2="104" y2="48" stroke="#E0B44C" stroke-width="2.5"/>
    <circle cx="104" cy="50" r="3.5" fill="#E0B44C"/>
  </g>
</svg>
"""

LEER_GRAFIK = """
<svg width="90" height="70" viewBox="0 0 90 70" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
  <rect x="14" y="14" width="58" height="40" rx="8" fill="#E2D6C3"/>
  <rect x="20" y="8" width="58" height="40" rx="8" fill="#FFFFFF" stroke="#B89B74" stroke-width="2"/>
  <rect x="30" y="20" width="36" height="4" rx="2" fill="#B89B74"/>
  <rect x="30" y="30" width="24" height="4" rx="2" fill="#E2D6C3"/>
</svg>
"""


def kopfbereich():
    st.markdown(STIL, unsafe_allow_html=True)
    st.markdown(
        f"""<div class="hero"><div><h1>Uniwolf</h1>
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

# App-Symbol (Browser-Tab): der Uniwolf-Kopf als Bild, sonst das Emoji
ICON = Path(__file__).parent / "uniwolf_icon.png"
st.set_page_config(page_title="Uniwolf", page_icon=str(ICON) if ICON.exists() else "🐺", layout="wide")
kopfbereich()
meldung_zeigen()
if ONLINE:
    anmeldung()

# --- Seitenleiste: Schlüssel und Modell ---
with st.sidebar:
    st.header("⚙️ Einstellungen")
    if ONLINE:
        st.caption(f"👤 Angemeldet als {st.session_state.get('user_email', '')}")
        if st.button("Abmelden"):
            abmelden()
    # Steht ANTHROPIC_API_KEY in den Secrets, wird er für alle benutzt (online: dein Schlüssel
    # für alle angemeldeten Nutzer). Sonst gibt man ihn hier selbst ein.
    vorbelegt = api_schluessel_vorbelegt()
    if vorbelegt:
        st.success("✅ Claude ist bereit. Du musst keinen Schlüssel eingeben.")
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

    if guthaben_pflicht(api_key):
        guthaben_seitenleiste()

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
            st.rerun()
    if "zusammenfassung" not in st.session_state:
        leer_hinweis("Noch keine Zusammenfassung. Klick auf den Button oben.")
    else:
        in_ordner_speichern("zusammenfassung")
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
            st.rerun()

    if not st.session_state.get("karten"):
        leer_hinweis("Noch keine Karteikarten. Wähle die Anzahl und klick auf den Button.")
    else:
        in_ordner_speichern("karten")
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
def fragetyp_umschalten(t: str) -> None:
    typen = st.session_state["fragetypen"]
    if t in typen:
        typen.remove(t)
    else:
        typen.append(t)


@st.fragment
def quiz_einstellungen() -> None:
    """Fragetypen, Anzahl und Erstellen-Knopf. Als Fragment lädt ein Klick nur diesen Teil neu."""
    st.markdown("**Welche Fragetypen möchtest du?** Klick auf die Felder (mehrere möglich).")
    if "fragetypen" not in st.session_state:
        st.session_state["fragetypen"] = list(FRAGETYPEN.keys())
    with st.container(key="typ_kacheln"):
        for spalte, t in zip(st.columns(3), FRAGETYPEN):
            aktiv = t in st.session_state["fragetypen"]
            haken = "✅ " if aktiv else ""
            spalte.button(
                f"{TYP_SYMBOL[t]}  \n**{haken}{TYP_TITEL[t]}**  \n{TYP_TEXT[t]}",
                key=f"typ_{t}", type="primary" if aktiv else "secondary", use_container_width=True,
                on_click=fragetyp_umschalten, args=(t,),
            )
    gewaehlte_typen = [t for t in FRAGETYPEN if t in st.session_state["fragetypen"]]
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
            st.rerun()  # ganze Seite: Quiz anzeigen und Ordner aktualisieren


with tab_quiz:
    quiz_einstellungen()

    if not st.session_state.get("quiz"):
        leer_hinweis("Noch kein Quiz. Wähle die Fragetypen und klick auf den Button.")
    else:
        in_ordner_speichern("quiz")
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
                    "Vergleiche mit der Musterlösung und den Unterlagen. Bewerte großzügig und nach dem Sinn, "
                    "nicht nach dem Wortlaut: Die Person soll zeigen, dass sie den Inhalt verstanden hat, "
                    "nicht die Folien auswendig wiedergeben. Eigene Worte, Umschreibungen, Beispiele, "
                    "Alltagssprache statt Fachbegriff, andere Reihenfolge, Stichpunkte sowie Tipp- und "
                    "Rechtschreibfehler sind in Ordnung. "
                    "'richtig': Der Kern der Musterlösung stimmt, auch wenn Nebensächliches oder Details fehlen. "
                    "'teilweise richtig': Ein wichtiger Teil stimmt, aber ein wesentlicher Punkt fehlt oder ist unklar. "
                    "'falsch': Nur wenn der Sinn falsch ist, ein klarer inhaltlicher Fehler vorliegt "
                    "oder die Antwort nichts mit der Frage zu tun hat. Im Zweifel die bessere Bewertung wählen. "
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
