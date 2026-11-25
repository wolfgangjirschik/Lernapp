-- Uniwolf: Öffentlich teilen
-- Einmal im Supabase SQL Editor ausführen (Editor vorher leeren, nur diesen Text einfügen).
--
-- Was es macht:
-- * Neue Tabelle "geteilt": eine Kopie von allem, was jemand auf "öffentlich" stellt
--   (ein Quiz, Karteikarten, eine Zusammenfassung oder ein ganzer Ordner).
-- * Regeln (Row Level Security): Alle angemeldeten Nutzer dürfen öffentliche Sachen LESEN.
--   Ändern, anlegen und löschen darf jeder nur seine EIGENEN Einträge.
--   Nicht angemeldete Besucher sehen gar nichts.
-- * Private Sachen kommen nie in diese Tabelle. Sie bleiben in "lern_daten",
--   die nur der Besitzer lesen kann (siehe supabase_einrichtung.sql).
-- * Es wird keine E-Mail-Adresse gespeichert oder anderen gezeigt.

create table if not exists public.geteilt (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null default auth.uid() references auth.users (id) on delete cascade,
  quelle_id text not null,
  art text not null check (art in ('dok', 'ordner')),
  name text not null check (char_length(name) <= 200),
  typ text,
  inhalt jsonb not null,
  geaendert timestamptz not null default now(),
  unique (user_id, quelle_id)
);

alter table public.geteilt enable row level security;

drop policy if exists "Geteiltes lesen" on public.geteilt;
drop policy if exists "Eigenes teilen" on public.geteilt;
drop policy if exists "Eigenes Geteiltes ändern" on public.geteilt;
drop policy if exists "Eigenes Geteiltes löschen" on public.geteilt;

create policy "Geteiltes lesen" on public.geteilt
  for select to authenticated using (true);

create policy "Eigenes teilen" on public.geteilt
  for insert to authenticated with check ((select auth.uid()) = user_id);

create policy "Eigenes Geteiltes ändern" on public.geteilt
  for update to authenticated
  using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id);

create policy "Eigenes Geteiltes löschen" on public.geteilt
  for delete to authenticated using ((select auth.uid()) = user_id);

revoke all on public.geteilt from anon;
grant select, insert, update, delete on public.geteilt to authenticated;

create index if not exists geteilt_geaendert on public.geteilt (geaendert desc);
