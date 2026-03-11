-- Run this in Supabase SQL Editor (one time setup)

create table if not exists purchases (
  id           bigserial primary key,
  date         date not null,
  manufacturer text not null,
  pieces       numeric not null,
  rate         numeric not null,
  total        numeric not null,
  notes        text default '',
  created_at   timestamptz default now()
);

create table if not exists bills (
  id           bigserial primary key,
  week_start   date not null,
  week_end     date not null,
  manufacturer text not null,
  pieces       numeric not null,
  amount       numeric not null,
  notes        text default '',
  created_at   timestamptz default now()
);

create table if not exists payments (
  id           bigserial primary key,
  date         date not null,
  manufacturer text not null,
  amount       numeric not null,
  notes        text default '',
  created_at   timestamptz default now()
);
