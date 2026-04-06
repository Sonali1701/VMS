from sqlalchemy import create_engine, Column, String, DateTime, Text, Integer, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from datetime import datetime
import os
from dotenv import load_dotenv

load_dotenv()

# Database Configuration - SQLite for development
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./vms_database.db")

# Create engine - SQLite specific settings
if DATABASE_URL.startswith("sqlite"):
    engine = create_engine(
        DATABASE_URL, 
        echo=True, 
        connect_args={"check_same_thread": False}  # SQLite specific
    )
else:
    # For MySQL/PostgreSQL
    engine = create_engine(DATABASE_URL, echo=True)

# Create session factory
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Base class for models
Base = declarative_base()

# Dependency to get database session
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# Job Model
class Job(Base):
    __tablename__ = "jobs"
    
    id = Column(String(50), primary_key=True)
    title = Column(String(200), nullable=False)
    description = Column(Text, nullable=False)
    department = Column(String(100), nullable=False)
    location = Column(String(200), nullable=False)
    employment_type = Column(String(50), nullable=False)
    salary_range = Column(String(100))
    posted_date = Column(DateTime, default=datetime.utcnow)
    status = Column(String(20), default="active")
    requirements = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationship with candidates
    candidates = relationship("Candidate", back_populates="job")

# Candidate Model
class Candidate(Base):
    __tablename__ = "candidates"
    
    id = Column(String(50), primary_key=True)
    name = Column(String(200), nullable=False)
    email = Column(String(200), nullable=False)
    phone = Column(String(50))
    job_id = Column(String(50), ForeignKey("jobs.id"), nullable=False)
    resume_path = Column(String(500), nullable=False)
    submitted_date = Column(DateTime, default=datetime.utcnow)
    status = Column(String(50), default="submitted")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationship with job
    job = relationship("Job", back_populates="candidates")

# Vendor Model (for future expansion)
class Vendor(Base):
    __tablename__ = "vendors"
    
    id = Column(String(50), primary_key=True)
    name = Column(String(200), nullable=False)
    email = Column(String(200), nullable=False, unique=True)
    phone = Column(String(50))
    company = Column(String(200))
    address = Column(Text)
    status = Column(String(20), default="active")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

# Activity Log Model
class ActivityLog(Base):
    __tablename__ = "activity_logs"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(50))
    action = Column(String(100), nullable=False)
    resource_type = Column(String(50))
    resource_id = Column(String(50))
    details = Column(Text)
    timestamp = Column(DateTime, default=datetime.utcnow)

# Create all tables
def create_tables():
    Base.metadata.create_all(bind=engine)

if __name__ == "__main__":
    create_tables()
    print("Database tables created successfully!")
