-- migrate.sql
-- Run once: ALTER existing event_participants table to match new schema

BEGIN;

-- 1. Add new columns (nullable first)
ALTER TABLE event_participants
    ADD COLUMN IF NOT EXISTS id         SERIAL,
    ADD COLUMN IF NOT EXISTS guest_name VARCHAR;

-- 2. Drop old composite primary key
ALTER TABLE event_participants DROP CONSTRAINT IF EXISTS event_participants_pkey;

-- 3. Make id the new primary key
ALTER TABLE event_participants ADD PRIMARY KEY (id);

-- 4. Restore unique constraints
ALTER TABLE event_participants
    ADD CONSTRAINT uq_event_user  UNIQUE (event_id, user_id),
    ADD CONSTRAINT uq_event_guest UNIQUE (event_id, guest_name);

-- 5. Make user_id nullable (guests won't have one)
ALTER TABLE event_participants ALTER COLUMN user_id DROP NOT NULL;

COMMIT;
