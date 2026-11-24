-- Guthaben für Uniwolf. Einmal im Supabase "SQL Editor" ausführen.
-- WICHTIG: In der letzten Zeile deine eigene E-Mail eintragen (die, mit der du dich in Uniwolf anmeldest).
--
-- Sicherheit: Nutzer dürfen ihr Guthaben nur LESEN. Ändern geht nur über die Funktionen unten:
--   guthaben_stand()     gibt beim ersten Mal 5 € Startguthaben (pro E-Mail nur einmal)
--   guthaben_abbuchen()  zieht Kosten ab (nur abziehen, nie dazugeben)
--   guthaben_aufladen()  nur für Admins (Tabelle guthaben_admins)

create table if not exists public.guthaben (
  user_id uuid primary key references auth.users (id) on delete cascade,
  email text not null unique,
  cent numeric(14,4) not null default 0,
  geaendert timestamptz not null default now()
);

create table if not exists public.guthaben_buchungen (
  id bigint generated always as identity primary key,
  user_id uuid not null references auth.users (id) on delete cascade,
  cent numeric(14,4) not null,
  grund text not null,
  erstellt timestamptz not null default now()
);

-- Merkt sich, wer schon Startguthaben bekommen hat (bleibt auch, wenn ein Konto gelöscht wird)
create table if not exists public.startguthaben_vergeben (email text primary key);
create table if not exists public.guthaben_admins (email text primary key);

alter table public.guthaben enable row level security;
alter table public.guthaben_buchungen enable row level security;
alter table public.startguthaben_vergeben enable row level security;
alter table public.guthaben_admins enable row level security;

-- Nur lesen, und nur die eigenen Zeilen
revoke all on public.guthaben, public.guthaben_buchungen, public.startguthaben_vergeben, public.guthaben_admins
  from anon, authenticated;
grant select on public.guthaben, public.guthaben_buchungen to authenticated;

drop policy if exists "Eigenes Guthaben lesen" on public.guthaben;
create policy "Eigenes Guthaben lesen" on public.guthaben
  for select to authenticated using (auth.uid() = user_id);
drop policy if exists "Eigene Buchungen lesen" on public.guthaben_buchungen;
create policy "Eigene Buchungen lesen" on public.guthaben_buchungen
  for select to authenticated using (auth.uid() = user_id);

-- Kontostand holen (legt beim ersten Mal das Konto mit 5 € an)
create or replace function public.guthaben_stand() returns numeric
language plpgsql security definer set search_path = public as $$
declare
  uid uuid := auth.uid();
  mail text := lower(auth.jwt() ->> 'email');
  start numeric := 0;
  stand numeric;
begin
  if uid is null then raise exception 'nicht angemeldet'; end if;
  if not exists (select 1 from guthaben where user_id = uid) then
    insert into startguthaben_vergeben (email) values (mail) on conflict do nothing;
    if found then start := 500; end if;  -- 500 Cent = 5 €
    insert into guthaben (user_id, email, cent) values (uid, mail, start) on conflict do nothing;
    if start > 0 then
      insert into guthaben_buchungen (user_id, cent, grund) values (uid, start, 'Startguthaben');
    end if;
  end if;
  select cent into stand from guthaben where user_id = uid;
  return stand;
end $$;

-- Kosten abziehen (nur positive Beträge bis 10 € pro Anfrage)
create or replace function public.guthaben_abbuchen(kosten_cent numeric, grund text) returns numeric
language plpgsql security definer set search_path = public as $$
declare stand numeric;
begin
  if auth.uid() is null then raise exception 'nicht angemeldet'; end if;
  if kosten_cent is null or kosten_cent < 0 or kosten_cent > 1000 then
    raise exception 'ungültiger Betrag';
  end if;
  update guthaben set cent = cent - kosten_cent, geaendert = now()
    where user_id = auth.uid() returning cent into stand;
  insert into guthaben_buchungen (user_id, cent, grund) values (auth.uid(), -kosten_cent, left(grund, 100));
  return stand;
end $$;

create or replace function public.ist_guthaben_admin() returns boolean
language sql security definer set search_path = public stable as $$
  select exists (select 1 from guthaben_admins where email = lower(auth.jwt() ->> 'email'));
$$;

-- Admin: Guthaben einer Person ändern (positiv = aufladen, negativ = abziehen)
create or replace function public.guthaben_aufladen(ziel_email text, betrag_cent numeric) returns numeric
language plpgsql security definer set search_path = public as $$
declare stand numeric; ziel uuid;
begin
  if not ist_guthaben_admin() then raise exception 'keine Berechtigung'; end if;
  if betrag_cent is null or betrag_cent = 0 or abs(betrag_cent) > 100000 then
    raise exception 'ungültiger Betrag';
  end if;
  update guthaben set cent = cent + betrag_cent, geaendert = now()
    where email = lower(ziel_email) returning cent, user_id into stand, ziel;
  if ziel is null then
    raise exception 'Kein Konto mit dieser E-Mail. Die Person muss sich zuerst einmal anmelden.';
  end if;
  insert into guthaben_buchungen (user_id, cent, grund) values (ziel, betrag_cent, 'Aufladung');
  return stand;
end $$;

-- Admin: alle Kontostände sehen
create or replace function public.guthaben_liste()
returns table (email text, cent numeric, geaendert timestamptz)
language plpgsql security definer set search_path = public as $$
begin
  if not ist_guthaben_admin() then raise exception 'keine Berechtigung'; end if;
  return query select g.email, g.cent, g.geaendert from guthaben g order by g.email;
end $$;

revoke execute on function public.guthaben_stand(), public.guthaben_abbuchen(numeric, text),
  public.ist_guthaben_admin(), public.guthaben_aufladen(text, numeric), public.guthaben_liste()
  from public, anon;
grant execute on function public.guthaben_stand(), public.guthaben_abbuchen(numeric, text),
  public.ist_guthaben_admin(), public.guthaben_aufladen(text, numeric), public.guthaben_liste()
  to authenticated;

-- HIER deine E-Mail eintragen (klein geschrieben):
insert into public.guthaben_admins (email) values ('deine-email@beispiel.at') on conflict do nothing;
