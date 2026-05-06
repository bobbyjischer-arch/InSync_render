-- migrate_email_verify.sql
-- Add pending_registrations and password_resets tables.
-- Run once after updating to this version.

CREATE TABLE IF NOT EXISTS pending_registrations (
    id            SERIAL PRIMARY KEY,
    first_name    VARCHAR NOT NULL,
    email         VARCHAR NOT NULL UNIQUE,
    password_hash VARCHAR NOT NULL,
    code          VARCHAR(6) NOT NULL,
    expires_at    TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS password_resets (
    id         SERIAL PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token      VARCHAR(64) NOT NULL UNIQUE,
    expires_at TIMESTAMPTZ NOT NULL,
    used       BOOLEAN NOT NULL DEFAULT FALSE
);
