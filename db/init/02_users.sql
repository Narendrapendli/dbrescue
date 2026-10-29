-- ==============================================================================
-- dbrescue Least-Privilege Role & User Provisions (02_users.sql)
-- Strict separation of concerns: App User, Backup Operator, and Restore Operator.
-- Root access is forbidden for day-to-day and application operations.
-- ==============================================================================

-- 1. Application User: 'rescue_app'@'%'
-- Limited strictly to student_db data manipulation (DML). Cannot alter schemas or access sys.
CREATE USER IF NOT EXISTS 'rescue_app'@'%' IDENTIFIED WITH caching_sha2_password BY 'RescuePass2026!Secure';
GRANT SELECT, INSERT, UPDATE, DELETE, CREATE TEMPORARY TABLES, LOCK TABLES ON student_db.* TO 'rescue_app'@'%';

-- 2. Dedicated Backup Operator: 'rescue_backup'@'%'
-- Limited to read-only logical dump operations (SELECT, LOCK TABLES, SHOW VIEW, TRIGGER, EVENT)
-- Global PROCESS privilege is granted to allow inspecting table locks during consistent mysqldump.
CREATE USER IF NOT EXISTS 'rescue_backup'@'%' IDENTIFIED WITH caching_sha2_password BY 'RescueBackup2026!Safe';
GRANT SELECT, LOCK TABLES, SHOW VIEW, TRIGGER, EVENT ON student_db.* TO 'rescue_backup'@'%';
GRANT PROCESS ON *.* TO 'rescue_backup'@'%';

-- 3. Dedicated Restore Operator: 'rescue_restore'@'%'
-- Isolated strictly to dbrescue_test and dbrescue_restore_% databases for disaster recovery sandbox dry-runs and tests.
-- Cannot modify student_db.
CREATE USER IF NOT EXISTS 'rescue_restore'@'%' IDENTIFIED WITH caching_sha2_password BY 'RescueRestore2026!DryRun';
GRANT ALL PRIVILEGES ON dbrescue_test.* TO 'rescue_restore'@'%';
GRANT ALL PRIVILEGES ON `dbrescue_restore_%`.* TO 'rescue_restore'@'%';

-- 4. Flush privileges to ensure runtime consistency
FLUSH PRIVILEGES;
