-- ==============================================================================
-- dbrescue Primary Relational Schema Initialization (01_schema.sql)
-- Defines student_db, dbrescue_test, and run-scoped test_records tables.
-- ==============================================================================

-- 1. Create Primary Business Database: student_db
CREATE DATABASE IF NOT EXISTS student_db
    CHARACTER SET utf8mb4
    COLLATE utf8mb4_0900_ai_ci;

USE student_db;

-- Students table
CREATE TABLE IF NOT EXISTS students (
    student_id BIGINT AUTO_INCREMENT PRIMARY KEY,
    student_uuid VARCHAR(36) NOT NULL UNIQUE,
    first_name VARCHAR(60) NOT NULL,
    last_name VARCHAR(60) NOT NULL,
    email VARCHAR(120) NOT NULL UNIQUE,
    major VARCHAR(80) NOT NULL DEFAULT 'Computer Science',
    gpa DECIMAL(3, 2) NOT NULL DEFAULT 3.00,
    status ENUM('ACTIVE', 'PROBATION', 'GRADUATED', 'WITHDRAWN') NOT NULL DEFAULT 'ACTIVE',
    enrolled_date DATE NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_students_status (status),
    INDEX idx_students_major (major),
    INDEX idx_students_enrolled (enrolled_date)
) ENGINE=InnoDB;

-- Courses table
CREATE TABLE IF NOT EXISTS courses (
    course_id BIGINT AUTO_INCREMENT PRIMARY KEY,
    course_code VARCHAR(20) NOT NULL UNIQUE,
    title VARCHAR(150) NOT NULL,
    department VARCHAR(80) NOT NULL,
    credits TINYINT UNSIGNED NOT NULL DEFAULT 3,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_courses_dept (department),
    INDEX idx_courses_active (is_active)
) ENGINE=InnoDB;

-- Enrollments table (Relational junction with foreign keys)
CREATE TABLE IF NOT EXISTS enrollments (
    enrollment_id BIGINT AUTO_INCREMENT PRIMARY KEY,
    student_id BIGINT NOT NULL,
    course_id BIGINT NOT NULL,
    semester VARCHAR(20) NOT NULL,
    grade VARCHAR(2) DEFAULT NULL,
    status ENUM('ENROLLED', 'COMPLETED', 'DROPPED') NOT NULL DEFAULT 'ENROLLED',
    enrolled_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_enrollments_student FOREIGN KEY (student_id)
        REFERENCES students(student_id) ON DELETE CASCADE,
    CONSTRAINT fk_enrollments_course FOREIGN KEY (course_id)
        REFERENCES courses(course_id) ON DELETE RESTRICT,
    UNIQUE KEY uq_student_course_semester (student_id, course_id, semester),
    INDEX idx_enrollments_student (student_id),
    INDEX idx_enrollments_course (course_id),
    INDEX idx_enrollments_semester (semester)
) ENGINE=InnoDB;

-- Run-scoped test records table (Tagged by run_id for recovery verification)
CREATE TABLE IF NOT EXISTS test_records (
    record_id BIGINT AUTO_INCREMENT PRIMARY KEY,
    run_id VARCHAR(64) NOT NULL,
    marker_name VARCHAR(100) NOT NULL,
    payload_hash VARCHAR(64) NOT NULL,
    payload_data TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_test_records_run (run_id),
    INDEX idx_test_records_marker (marker_name)
) ENGINE=InnoDB;


-- 2. Create Isolated Disaster Recovery Test Database: dbrescue_test
CREATE DATABASE IF NOT EXISTS dbrescue_test
    CHARACTER SET utf8mb4
    COLLATE utf8mb4_0900_ai_ci;

USE dbrescue_test;

-- Mirror test_records in dbrescue_test to validate restore roundtrips
CREATE TABLE IF NOT EXISTS test_records (
    record_id BIGINT AUTO_INCREMENT PRIMARY KEY,
    run_id VARCHAR(64) NOT NULL,
    marker_name VARCHAR(100) NOT NULL,
    payload_hash VARCHAR(64) NOT NULL,
    payload_data TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_test_records_run (run_id)
) ENGINE=InnoDB;

-- Schema migration tracking in restore test database
CREATE TABLE IF NOT EXISTS restore_audit_metadata (
    id INT AUTO_INCREMENT PRIMARY KEY,
    source_backup_id VARCHAR(64) NOT NULL,
    restored_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    verified_tables INT NOT NULL DEFAULT 0,
    verified_rows INT NOT NULL DEFAULT 0
) ENGINE=InnoDB;
