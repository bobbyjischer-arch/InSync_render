-- migrate_email.sql
-- Rename the 'phone' column to 'email' in the users table.
-- Run once after updating to the new codebase.

BEGIN;

-- Rename column
ALTER TABLE users RENAME COLUMN phone TO email;

-- Update the unique constraint name for clarity (optional but clean)
ALTER INDEX IF EXISTS users_phone_key RENAME TO users_email_key;

COMMIT;
