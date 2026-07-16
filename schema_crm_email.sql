-- schema_crm_email.sql
-- CRM Contact database + Email Marketing module

-- ──────────────────────────────────────────────────────────
-- 1. CONTACT (company level)
-- ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS contact (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_id       UUID NOT NULL REFERENCES entity(id),
    company_name    VARCHAR(255) NOT NULL,
    industry        VARCHAR(100),
    website         VARCHAR(255),
    address         TEXT,
    city            VARCHAR(100),
    province        VARCHAR(100),
    country         VARCHAR(100) DEFAULT 'Indonesia',
    phone           VARCHAR(50),
    email           VARCHAR(255),
    source          VARCHAR(50) DEFAULT 'cold_call',
    -- cold_call, referral, event, website, social_media, other
    status          VARCHAR(30) DEFAULT 'prospect',
    -- prospect, active, inactive, lost
    assigned_to     VARCHAR(100),
    notes           TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ──────────────────────────────────────────────────────────
-- 2. CONTACT PERSON (person under company)
-- ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS contact_person (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    contact_id          UUID NOT NULL REFERENCES contact(id) ON DELETE CASCADE,
    full_name           VARCHAR(255) NOT NULL,
    title               VARCHAR(100),
    department          VARCHAR(100),
    email               VARCHAR(255),
    phone               VARCHAR(50),
    whatsapp            VARCHAR(50),
    linkedin            VARCHAR(255),
    is_primary          BOOLEAN DEFAULT FALSE,
    is_decision_maker   BOOLEAN DEFAULT FALSE,
    notes               TEXT,
    created_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_contact_person_contact_id ON contact_person(contact_id);
CREATE INDEX IF NOT EXISTS idx_contact_entity ON contact(entity_id);

-- ──────────────────────────────────────────────────────────
-- 3. EMAIL CAMPAIGN
-- ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS email_campaign (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_id       UUID NOT NULL REFERENCES entity(id),
    name            VARCHAR(255) NOT NULL,
    subject         VARCHAR(500) NOT NULL,
    body_html       TEXT NOT NULL,
    status          VARCHAR(30) DEFAULT 'draft',
    -- draft, scheduled, sending, sent, cancelled
    scheduled_at    TIMESTAMPTZ,
    sent_at         TIMESTAMPTZ,
    created_by      VARCHAR(100),
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ──────────────────────────────────────────────────────────
-- 4. CAMPAIGN RECIPIENTS (per-person tracking)
-- ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS email_campaign_recipient (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    campaign_id         UUID NOT NULL REFERENCES email_campaign(id) ON DELETE CASCADE,
    contact_person_id   UUID REFERENCES contact_person(id),
    recipient_email     VARCHAR(255) NOT NULL,
    recipient_name      VARCHAR(255),
    company_name        VARCHAR(255),
    status              VARCHAR(30) DEFAULT 'pending',
    -- pending, sent, failed, bounced, unsubscribed
    sent_at             TIMESTAMPTZ,
    opened_at           TIMESTAMPTZ,
    open_count          INTEGER DEFAULT 0,
    tracking_token      UUID DEFAULT gen_random_uuid() UNIQUE,
    error_message       TEXT,
    created_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ecr_campaign ON email_campaign_recipient(campaign_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_ecr_token ON email_campaign_recipient(tracking_token);
