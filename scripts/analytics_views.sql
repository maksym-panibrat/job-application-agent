-- Analytics views for the in-app event log (spec section 7).
--
-- Apply once with:
--   psql $DATABASE_URL -f scripts/analytics_views.sql
--
-- Re-runnable: every view uses CREATE OR REPLACE.

-- 1. Onboarding funnel: first time per profile that each milestone fired.
CREATE OR REPLACE VIEW analytics_onboarding_funnel AS
WITH steps AS (
  SELECT
    profile_id,
    MIN(occurred_at) FILTER (WHERE name = 'auth.signin_succeeded')          AS signed_in_at,
    MIN(occurred_at) FILTER (WHERE name = 'profile.coach_opened_from_card') AS coach_first_open,
    MIN(occurred_at) FILTER (WHERE name = 'feed.sync_clicked')              AS first_sync_at,
    MIN(occurred_at) FILTER (WHERE name = 'match.card_opened')              AS first_match_open,
    MIN(occurred_at) FILTER (WHERE name = 'match.applied')                  AS first_apply_at
  FROM events
  GROUP BY profile_id
)
SELECT * FROM steps WHERE profile_id IS NOT NULL;

-- 2. Per-event usage in the trailing 30 days.
CREATE OR REPLACE VIEW analytics_feature_usage_30d AS
SELECT
  name,
  count(*)                       AS occurrences,
  count(DISTINCT session_id)     AS sessions,
  count(DISTINCT profile_id)     AS profiles
FROM events
WHERE occurred_at > now() - interval '30 days'
GROUP BY name
ORDER BY occurrences DESC;

-- 3. Match-dismiss patterns by source and score.
CREATE OR REPLACE VIEW analytics_dismiss_patterns AS
SELECT
  properties->>'source' AS source,
  ROUND((properties->>'score')::numeric, 2) AS score,
  count(*) AS dismissals
FROM events
WHERE name = 'match.dismissed'
  AND occurred_at > now() - interval '30 days'
GROUP BY source, score
ORDER BY dismissals DESC;

-- 4. Cover-letter funnel per application.
CREATE OR REPLACE VIEW analytics_cover_letter_funnel AS
WITH per_app AS (
  SELECT
    (properties->>'application_id')::uuid AS application_id,
    MAX(CASE WHEN name = 'cover_letter.generation_clicked'  THEN 1 ELSE 0 END) AS clicked,
    MAX(CASE WHEN name = 'cover_letter.generation_succeeded' THEN 1 ELSE 0 END) AS succeeded,
    MAX(CASE WHEN name = 'cover_letter.edited'              THEN 1 ELSE 0 END) AS edited,
    MAX(CASE WHEN name = 'cover_letter.pdf_downloaded'      THEN 1 ELSE 0 END) AS downloaded,
    MAX(CASE WHEN name = 'match.applied'                    THEN 1 ELSE 0 END) AS applied
  FROM events
  WHERE name IN (
    'cover_letter.generation_clicked',
    'cover_letter.generation_succeeded',
    'cover_letter.edited',
    'cover_letter.pdf_downloaded',
    'match.applied'
  )
  AND properties ? 'application_id'
  GROUP BY application_id
)
SELECT
  count(*)                   AS apps_with_activity,
  sum(clicked)               AS clicked,
  sum(succeeded)             AS succeeded,
  sum(edited)                AS edited,
  sum(downloaded)            AS downloaded,
  sum(applied)               AS applied
FROM per_app;

-- 5. Sync friction over the trailing 30 days, daily.
CREATE OR REPLACE VIEW analytics_sync_friction_30d AS
SELECT
  date_trunc('day', occurred_at) AS day,
  sum(CASE WHEN name = 'feed.sync_clicked'    THEN 1 ELSE 0 END) AS clicks,
  sum(CASE WHEN name = 'feed.sync_succeeded'  THEN 1 ELSE 0 END) AS successes,
  sum(CASE WHEN name = 'feed.sync_failed'     THEN 1 ELSE 0 END) AS failures
FROM events
WHERE occurred_at > now() - interval '30 days'
GROUP BY 1
ORDER BY 1;
