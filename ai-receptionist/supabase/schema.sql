-- Paste this whole file into Supabase → SQL Editor → Run.

create table if not exists conversations (
  id          text primary key,
  channel     text not null default 'chat',
  source_url  text,
  started_at  timestamptz not null default now()
);

create table if not exists messages (
  id              bigserial primary key,
  conversation_id text not null references conversations(id) on delete cascade,
  role            text not null check (role in ('user', 'assistant')),
  content         text not null,
  created_at      timestamptz not null default now()
);

create table if not exists leads (
  id              uuid primary key default gen_random_uuid(),
  conversation_id text unique not null references conversations(id) on delete cascade,
  name            text,
  email           text,
  phone           text,
  budget_min      bigint,
  budget_max      bigint,
  neighborhood    text,
  property_type   text,
  bedrooms        int,
  timeline        text,
  notes           text,
  booked_at       timestamptz,
  created_at      timestamptz not null default now()
);

create index if not exists messages_conversation_idx on messages(conversation_id);
create index if not exists conversations_started_idx on conversations(started_at desc);

-- The app talks to Supabase with the service role key from the server only,
-- so row level security stays on with no public policies.
alter table conversations enable row level security;
alter table messages      enable row level security;
alter table leads         enable row level security;
