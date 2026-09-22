-- Incremental rollup refresh + raw retention. Run every 5 min via
-- house-power-rollup.timer. Recomputes only recent windows (cheap at 1 row/s)
-- and deletes raw readings older than 90 days. Aggregates are kept forever.
BEGIN;

DELETE FROM power_1min WHERE bucket >= now() - INTERVAL '2 hours';
INSERT INTO power_1min
SELECT date_trunc('minute', ts), host,
       AVG(leg1_a), MAX(leg1_a), AVG(leg2_a), MAX(leg2_a), COUNT(*)
FROM readings
WHERE ts >= now() - INTERVAL '2 hours'
  AND ts < date_trunc('minute', now())
GROUP BY 1, 2;

DELETE FROM power_1hour WHERE bucket >= now() - INTERVAL '2 days';
INSERT INTO power_1hour
SELECT date_trunc('hour', ts), host,
       AVG(leg1_a), MAX(leg1_a), AVG(leg2_a), MAX(leg2_a), COUNT(*)
FROM readings
WHERE ts >= now() - INTERVAL '2 days'
  AND ts < date_trunc('hour', now())
GROUP BY 1, 2;

DELETE FROM power_day WHERE bucket >= now() - INTERVAL '60 days';
INSERT INTO power_day
SELECT date_trunc('day', ts), host,
       AVG(leg1_a), MAX(leg1_a), AVG(leg2_a), MAX(leg2_a), COUNT(*)
FROM readings
WHERE ts >= now() - INTERVAL '60 days'
  AND ts < date_trunc('day', now())
GROUP BY 1, 2;

DELETE FROM readings WHERE ts < now() - INTERVAL '90 days';

COMMIT;
