-- Einmal im Supabase "SQL Editor" ausführen.
-- Legt eine Tabelle an, in der jede Person nur ihre eigenen Daten sehen und ändern kann.

create table if not exists public.lern_daten (
  user_id uuid primary key default auth.uid() references auth.users (id) on delete cascade,
  daten jsonb not null default '{}'::jsonb,
  geaendert timestamptz not null default now()
);

alter table public.lern_daten enable row level security;

create policy "Eigene Daten lesen" on public.lern_daten
  for select to authenticated using (auth.uid() = user_id);

create policy "Eigene Daten anlegen" on public.lern_daten
  for insert to authenticated with check (auth.uid() = user_id);

create policy "Eigene Daten ändern" on public.lern_daten
  for update to authenticated using (auth.uid() = user_id) with check (auth.uid() = user_id);

grant select, insert, update on public.lern_daten to authenticated;
