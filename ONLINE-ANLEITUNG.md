# Lern-App online stellen (für Freunde)

Am Ende hast du einen Link wie `https://deine-lern-app.streamlit.app`.
Deine Freunde öffnen ihn und legen ein Konto an (E-Mail + Passwort).
Einen API-Schlüssel brauchen sie nicht: Die App nutzt deinen Schlüssel.
Jeder sieht nur seine eigenen Ordner.

Alles ist kostenlos. Du brauchst drei Konten. Dauer: etwa 30 Minuten.

| Dienst | Wofür | Kosten |
|---|---|---|
| GitHub | Speichert den Programmcode | kostenlos |
| Supabase | Login und Speicher für Ordner/Quizze | kostenlos (Free Plan) |
| Streamlit Community Cloud | Macht die App im Internet erreichbar | kostenlos |

Deine lokale Version auf dem Mac funktioniert weiter wie bisher.

---

## Schritt 1: GitHub (Code-Speicher)

1. Gehe auf https://github.com/signup und lege ein Konto an.
2. Oben rechts auf **+** → **New repository** klicken.
3. Erledigt: Dein Repository heißt `wolfgangjirschik/Lernapp`, der Code ist schon hochgeladen.

**Wichtig:** Die Datei `.streamlit/secrets.toml` und der Ordner `meine_daten`
kommen nie zu GitHub. Das ist in der Datei `.gitignore` so eingestellt.

---

## Schritt 2: Supabase (Login und Datenbank)

1. Gehe auf https://supabase.com und klicke **Start your project**.
   Am einfachsten meldest du dich mit deinem GitHub-Konto an.
2. **New project** klicken:
   - Name: `lern-app`
   - Database Password: ein sicheres Passwort ausdenken und notieren
   - Region: **Central EU (Frankfurt)**
   - Auf **Create new project** klicken und 1–2 Minuten warten.
3. Tabelle anlegen: Links auf **SQL Editor** klicken. Den ganzen Inhalt der Datei
   `supabase_einrichtung.sql` (im Ordner lern-app) hineinkopieren und auf **Run** klicken.
   Es sollte „Success“ erscheinen.
4. E-Mail-Bestätigung ausschalten (sonst müssen Freunde erst einen Link in der
   E-Mail anklicken): Links **Authentication** → **Sign In / Providers** → **Email**
   → **Confirm email** ausschalten → **Save**.
   Falls die Menüs etwas anders heißen: Es ist die Seite „Auth Providers“.
5. Zugangsdaten kopieren: Links **Project Settings** (Zahnrad) → **API Keys**
   bzw. **Data API**. Du brauchst zwei Werte:
   - die **Project URL** (sieht aus wie `https://abcd1234.supabase.co`)
   - den **publishable** Schlüssel (beginnt mit `sb_publishable_`) oder,
     falls nur dieser angezeigt wird, den **anon public** Schlüssel.
   Den **secret** bzw. **service_role** Schlüssel bitte **nicht** verwenden.

---

## Schritt 3: Streamlit Community Cloud (App ins Internet)

1. Gehe auf https://share.streamlit.io und melde dich **mit GitHub** an.
2. **Create app** klicken und die Variante mit GitHub-Repository wählen.
3. Repository: `wolfgangjirschik/Lernapp`, Branch: `main`, Main file path: `app.py`.
4. Bei **App URL** kannst du dir einen Namen aussuchen, z. B. `wolfgangs-lern-app`.
5. **Advanced settings** öffnen:
   - Python version: **3.12** oder neuer
   - Bei **Secrets** genau das eintragen (mit deinen Werten aus Schritt 2):
     ```
     SUPABASE_URL = "https://abcd1234.supabase.co"
     SUPABASE_KEY = "sb_publishable_..."
     ```
   - Dazu deinen Claude-Schlüssel, damit niemand einen eingeben muss:
     ```
     ANTHROPIC_API_KEY = "sk-ant-..."
     ```
     Dann lernen alle angemeldeten Nutzer **auf deine Kosten**. Setze deshalb
     unbedingt ein Ausgabenlimit (siehe „Gut zu wissen“).
   - Optional, sehr empfohlen: Nur bestimmte E-Mails zulassen, damit Fremde mit
     dem Link kein Konto anlegen können:
     ```
     ERLAUBTE_EMAILS = ["du@beispiel.at", "freund@beispiel.at"]
     ```
     Neue Freunde fügst du später unter **App settings → Secrets** hinzu.
   - Optional: Ein Knopf **☕ Unterstützen** in der Seitenleiste, über den Freunde dir
     etwas schicken können (dein Revolut-Zahlungslink):
     ```
     REVOLUT_LINK = "https://revolut.me/dein-name"
     ```
     Ohne diese Zeile erscheint kein Knopf.
   - **Save** klicken.
6. **Deploy** klicken. Nach 2–3 Minuten ist die App online.
7. Den Link schickst du deinen Freunden.

---

## Was deine Freunde tun

1. Link öffnen → Tab **Neues Konto** → E-Mail und Passwort → **Konto erstellen**.
2. Ordner anlegen und loslernen. Ein API-Schlüssel ist nicht nötig.

---

## Gut zu wissen

- **Pause bei Nichtbenutzung:** Supabase pausiert kostenlose Projekte, wenn sie
  7 Tage kaum benutzt wurden. Du bekommst vorher eine E-Mail. Wieder einschalten:
  im Supabase-Dashboard das Projekt öffnen → **Resume project**. Die Daten bleiben erhalten.
- **Grenzen im Free Plan:** 2 aktive Projekte, 500 MB Datenbank, 50.000 aktive
  Nutzer pro Monat. Das reicht für dich und deine Freunde locker.
- **Ausgabenlimit setzen:** Öffne https://platform.claude.com/settings/spend-limits,
  klicke neben **Monthly spend limit** auf **Set limit** bzw. **Adjust limit** und
  gib z. B. 10 $ ein. Ist das Limit erreicht, kann die App nichts mehr erstellen,
  bis du es erhöhst oder ein neuer Monat beginnt. Lade außerdem nur so viel
  Guthaben auf, wie du ausgeben willst.
- **Günstiger lernen:** Opus 5.5 kostet pro Anfrage etwa doppelt so viel wie
  Sonnet 5.5. Sag deinen Freunden ruhig, dass sie Sonnet wählen sollen.
- **Datenschutz:** Ordner und Quizze deiner Freunde liegen bei Supabase
  (Server in Frankfurt, wenn du die Region so wählst). Dein API-Schlüssel
  liegt nur in den Streamlit-Secrets und ist für Nutzer nicht sichtbar.
- **Passwort vergessen:** gibt es in dieser Version noch nicht. Du kannst
  Nutzer im Supabase-Dashboard unter **Authentication → Users** verwalten.

---

## Quellen

- Supabase Preise und Free Plan: https://supabase.com/pricing
- Supabase Pausieren nach 7 Tagen, Daten bleiben erhalten: https://supabase.com/docs/guides/platform/free-project-pausing
- Supabase Login mit E-Mail/Passwort, E-Mail-Bestätigung standardmäßig an: https://supabase.com/docs/guides/auth/passwords
- Streamlit Community Cloud ist kostenlos und nutzt GitHub (auch private Repositories): https://docs.streamlit.io/deploy/streamlit-community-cloud
- Ausgabenlimits in der Claude Console: https://platform.claude.com/docs/en/api/rate-limits#setting-your-own-spend-limit
- Revolut Zahlungslink erstellen (Zahlungen > Neu > Link > Geld anfordern): https://help.revolut.com/help/adding-money/with-money-from-friends-or-relatives/requesting-money/
- Secrets in Streamlit Community Cloud eintragen: https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/secrets-management
