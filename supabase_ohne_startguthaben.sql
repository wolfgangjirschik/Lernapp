-- Uniwolf: Startguthaben abschalten. Einmal im Supabase "SQL Editor" ausführen.
-- Neue Konten starten danach mit 0 €. Bestehende Kontostände bleiben unverändert.
create or replace function public.guthaben_stand() returns numeric
language plpgsql security definer set search_path = public as $$
declare
  uid uuid := auth.uid();
  mail text := lower(auth.jwt() ->> 'email');
  stand numeric;
begin
  if uid is null then raise exception 'nicht angemeldet'; end if;
  insert into guthaben (user_id, email, cent) values (uid, mail, 0) on conflict do nothing;
  select cent into stand from guthaben where user_id = uid;
  return stand;
end $$;
