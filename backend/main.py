from fastapi import FastAPI, HTTPException, UploadFile, File, Depends, status, Form, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.security import HTTPBearer, HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import List, Optional
import httpx
import os
from datetime import datetime, timedelta
import aiofiles
import json
from dotenv import load_dotenv
import re
from sqlalchemy import create_engine, Column, String, DateTime, Integer, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session, relationship
import bcrypt
from jose import JWTError, jwt
from uuid import uuid4
import asyncio
from pymongo import MongoClient
from pymongo.server_api import ServerApi

# Load environment variables
load_dotenv()

# MongoDB setup for persistent storage (works on Render free tier)
MONGODB_URI = os.getenv("MONGODB_URI", "")
mongo_client = None
db = None
users_collection = None
whitelist_collection = None

def init_mongodb():
    """Initialize MongoDB connection"""
    global mongo_client, db, users_collection, whitelist_collection
    
    if not MONGODB_URI:
        print("[MongoDB] No MONGODB_URI set, using fallback JSON storage")
        return False
    
    try:
        mongo_client = MongoClient(MONGODB_URI, server_api=ServerApi('1'))
        db = mongo_client.vms
        users_collection = db.users
        whitelist_collection = db.whitelist
        
        # Test connection
        mongo_client.admin.command('ping')
        print("[MongoDB] Connected successfully")
        return True
    except Exception as e:
        print(f"[MongoDB] Connection failed: {e}")
        return False

# Initialize MongoDB on startup
mongodb_enabled = init_mongodb()

# Load whitelisted users from Users file
WHITELISTED_USERS = set()
USERS_FILE_PATH = os.path.join(os.path.dirname(__file__), "..", "Users")

def load_whitelisted_users():
    """Load whitelisted users from MongoDB or fallback to Users file"""
    global WHITELISTED_USERS
    WHITELISTED_USERS = set()
    loaded_from_mongodb = False
    
    # Try MongoDB first
    if mongodb_enabled and whitelist_collection:
        try:
            for doc in whitelist_collection.find():
                WHITELISTED_USERS.add(doc["email"].lower())
            print(f"[Auth] Loaded {len(WHITELISTED_USERS)} whitelisted users from MongoDB")
            loaded_from_mongodb = True
            # Sync to file to keep them in sync
            try:
                with open(USERS_FILE_PATH, "w") as f:
                    for email in sorted(WHITELISTED_USERS):
                        f.write(f"{email}\n")
                print(f"[Auth] Synced {len(WHITELISTED_USERS)} whitelisted users to file")
            except Exception as e:
                print(f"[Auth] Error syncing to file: {e}")
        except Exception as e:
            print(f"[Auth] Error loading from MongoDB: {e}, falling back to file")
    
    # Fallback to file if MongoDB failed or not enabled
    if not loaded_from_mongodb:
        try:
            if os.path.exists(USERS_FILE_PATH):
                with open(USERS_FILE_PATH, "r") as f:
                    for line in f:
                        email = line.strip()
                        if email and not email.startswith("#"):
                            WHITELISTED_USERS.add(email.lower())
            print(f"[Auth] Loaded {len(WHITELISTED_USERS)} whitelisted users from file")
        except Exception as e:
            print(f"[Auth] Error loading Users file: {e}")

def save_whitelisted_users():
    """Save whitelisted users to MongoDB AND file for redundancy"""
    success = False
    
    # Try MongoDB first
    if mongodb_enabled and whitelist_collection:
        try:
            # Clear and re-insert all emails
            whitelist_collection.delete_many({})
            for email in WHITELISTED_USERS:
                whitelist_collection.insert_one({"email": email.lower()})
            print(f"[Auth] Saved {len(WHITELISTED_USERS)} whitelisted users to MongoDB")
            success = True
        except Exception as e:
            print(f"[Auth] Error saving to MongoDB: {e}")
    
    # ALWAYS save to file as backup (even if MongoDB succeeded)
    try:
        with open(USERS_FILE_PATH, "w") as f:
            for email in sorted(WHITELISTED_USERS):
                f.write(f"{email}\n")
        print(f"[Auth] Saved {len(WHITELISTED_USERS)} whitelisted users to file")
        success = True
    except Exception as e:
        print(f"[Auth] Error saving Users file: {e}")
    
    return success

# Initial load
load_whitelisted_users()

# JSON-based user storage for persistence without disk
USERS_JSON_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "users.json")

def load_users_from_json():
    """Load users from MongoDB or fallback to JSON file"""
    # Try MongoDB first
    if mongodb_enabled and users_collection:
        try:
            users = {}
            for doc in users_collection.find():
                email = doc["email"].lower()
                users[email] = {
                    "id": doc["id"],
                    "email": doc["email"],
                    "full_name": doc["full_name"],
                    "hashed_password": doc["hashed_password"],
                    "is_active": doc["is_active"],
                    "created_at": doc.get("created_at", datetime.now().isoformat())
                }
            print(f"[Auth] Loaded {len(users)} users from MongoDB")
            return users
        except Exception as e:
            print(f"[Auth] Error loading users from MongoDB: {e}, falling back to JSON")
    
    # Fallback to JSON file
    if os.path.exists(USERS_JSON_FILE):
        try:
            with open(USERS_JSON_FILE, "r") as f:
                return json.load(f)
        except Exception as e:
            print(f"[Auth] Error loading users JSON: {e}")
    return {}

def save_users_to_json(users):
    """Save users to MongoDB or fallback to JSON file"""
    # Try MongoDB first
    if mongodb_enabled and users_collection:
        try:
            # Update each user individually (upsert)
            for email, user_data in users.items():
                users_collection.update_one(
                    {"email": email.lower()},
                    {"$set": {
                        "id": user_data["id"],
                        "email": user_data["email"],
                        "full_name": user_data["full_name"],
                        "hashed_password": user_data["hashed_password"],
                        "is_active": user_data["is_active"],
                        "created_at": user_data.get("created_at", datetime.now().isoformat())
                    }},
                    upsert=True
                )
            print(f"[Auth] Saved {len(users)} users to MongoDB")
            return True
        except Exception as e:
            print(f"[Auth] Error saving users to MongoDB: {e}, falling back to JSON")
    
    # Fallback to JSON file
    try:
        os.makedirs(os.path.dirname(USERS_JSON_FILE), exist_ok=True)
        with open(USERS_JSON_FILE, "w") as f:
            json.dump(users, f, indent=2)
        return True
    except Exception as e:
        print(f"[Auth] Error saving users JSON: {e}")
        return False

# In-memory user cache (loaded from MongoDB/JSON on startup)
_users_cache = load_users_from_json()

# Database setup
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:////opt/render/project/src/data/vms.db")
# Ensure directory exists for SQLite
if DATABASE_URL.startswith("sqlite://"):
    # Extract path from sqlite:// URL
    db_path = DATABASE_URL.replace("sqlite://", "")
    if db_path.startswith("/"):
        # Absolute path
        db_dir = os.path.dirname(db_path)
    else:
        # Relative path (remove leading / if present after protocol)
        db_path = db_path.lstrip("/")
        db_dir = os.path.dirname(db_path)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# JWT Configuration
SECRET_KEY = os.getenv("SECRET_KEY", "your-secret-key-change-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30


# Password hashing - use bcrypt directly (passlib incompatible with bcrypt 4.x)
def verify_password(plain_password: str, hashed_password: str) -> bool:
    # bcrypt has 72-byte limit, truncate if necessary
    password_bytes = plain_password.encode('utf-8')
    if len(password_bytes) > 72:
        password_bytes = password_bytes[:72]
    hashed_bytes = hashed_password.encode('utf-8')
    return bcrypt.checkpw(password_bytes, hashed_bytes)

def get_password_hash(password: str) -> str:
    # bcrypt has 72-byte limit, truncate if necessary
    password_bytes = password.encode('utf-8')
    if len(password_bytes) > 72:
        password_bytes = password_bytes[:72]
    hashed = bcrypt.hashpw(password_bytes, bcrypt.gensalt())
    return hashed.decode('utf-8')


# Database Models
class UserDB(Base):
    __tablename__ = "users"
    
    id = Column(String, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    full_name = Column(String, nullable=False)
    hashed_password = Column(String, nullable=False)
    is_active = Column(String, default="true")
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationship
    submissions = relationship("CandidateDB", back_populates="submitted_by")

class CandidateDB(Base):
    __tablename__ = "candidates"
    
    id = Column(String, primary_key=True, index=True)
    name = Column(String, nullable=False)
    email = Column(String, nullable=False)
    phone = Column(String, nullable=False)
    job_id = Column(String, nullable=False)
    resume_path = Column(String, nullable=False)
    submitted_date = Column(DateTime, default=datetime.utcnow)
    status = Column(String, default="submitted")
    # Track who submitted this candidate
    submitted_by_user_id = Column(String, ForeignKey("users.id"), nullable=False)
    submitted_by = relationship("UserDB", back_populates="submissions")
    # New fields - all required
    bill_rate = Column(String, nullable=False)
    current_location = Column(String, nullable=False)
    primary_skills = Column(String, nullable=False)
    job_title = Column(String, nullable=False)
    years_experience = Column(String, nullable=False)
    tentative_start_date = Column(String, nullable=False)
    rto = Column(String, nullable=False)
    candidate_summary = Column(String, nullable=False)

# Create tables
Base.metadata.create_all(bind=engine)

# Log database status on startup
try:
    db_path = DATABASE_URL.replace("sqlite://", "").lstrip("/")
    if not db_path.startswith("/"):
        db_path = "/" + db_path
    if os.path.exists(db_path):
        db_size = os.path.getsize(db_path)
        print(f"[DB] Database exists: {db_path} ({db_size} bytes)")
    else:
        print(f"[DB] Database will be created at: {db_path}")
except Exception as e:
    print(f"[DB] Error checking database: {e}")

# Dependency to get DB session
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

app = FastAPI(title="VMS Backend API", version="1.0.0")

# Background task to fetch jobs continuously
async def scheduled_job_fetch():
    """Fetch jobs every 5 minutes in background"""
    while True:
        try:
            print("[Scheduled] Starting background job fetch...")
            await ceipal_client.fetch_all_jobs_background()
            print("[Scheduled] Background job fetch completed")
        except Exception as e:
            print(f"[Scheduled] Error in background fetch: {e}")
        
        # Wait 5 minutes before next fetch
        await asyncio.sleep(300)

@app.on_event("startup")
async def startup_event():
    """Start background job fetch on startup"""
    print("[Startup] Starting scheduled background job fetch...")
    # Start the continuous background fetch task
    asyncio.create_task(scheduled_job_fetch())

# Web UI (HTML/CSS/JS)
WEB_DIR = os.path.join(os.path.dirname(__file__), "web")
ASSETS_DIR = os.path.join(WEB_DIR, "assets")
if os.path.isdir(ASSETS_DIR):
    app.mount("/assets", StaticFiles(directory=ASSETS_DIR), name="assets")

# CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Security
security = HTTPBearer()


@app.get("/")
async def serve_web_app():
    index_path = os.path.join(WEB_DIR, "index.html")
    if not os.path.isfile(index_path):
        raise HTTPException(status_code=404, detail="Web UI not found")
    return FileResponse(index_path)

# Configuration
ATS_API_BASE_URL = os.getenv("ATS_API_BASE_URL", "https://api.ats-provider.com/v1")
ATS_API_KEY = os.getenv("ATS_API_KEY", "")

# Ceipal API Configuration
CEIPAL_AUTH_URL = os.getenv("CEIPAL_AUTH_URL", "https://api.ceipal.com/v1/createAuthtoken/")
# New API endpoint with 50 limit
CEIPAL_REPORTS_URL = os.getenv("CEIPAL_REPORTS_URL", "https://bi.ceipal.com/ReportDetails/getReportsData/ekZMUmhQVVhCNzRhbzcwcEpwZnN6Zz09")
CEIPAL_EMAIL = os.getenv("CEIPAL_EMAIL", "amir@radixsol.com")
CEIPAL_PASSWORD = os.getenv("CEIPAL_PASSWORD", "")
CEIPAL_API_KEY = os.getenv("CEIPAL_API_KEY", "2693f0ed28f2250811fe40294e97e108a56afa9043e5336da4")
CEIPAL_CACHE_DIR = os.getenv("CEIPAL_CACHE_DIR", "/opt/render/project/src/data/cache")
DEBUG = os.getenv("DEBUG", "False").lower() in {"1", "true", "yes", "y"}

UPLOAD_DIR = os.getenv("UPLOAD_DIR", "/opt/render/project/src/data/uploads")
MAX_FILE_SIZE = int(os.getenv("MAX_FILE_SIZE", 10485760))  # 10MB

# Ensure upload directory exists
os.makedirs(UPLOAD_DIR, exist_ok=True)

# Pydantic Models
class Job(BaseModel):
    id: str
    title: str
    description: str
    requirements: Optional[str] = None
    department: str
    location: str
    employment_type: str
    salary_range: Optional[str] = None
    posted_date: datetime
    status: str
    end_client: Optional[str] = None  # End client from Ceipal

class Candidate(BaseModel):
    id: str
    name: str
    email: str
    phone: Optional[str] = None
    job_id: str
    resume_path: str
    submitted_date: datetime
    status: str = "submitted"

class JobListResponse(BaseModel):
    jobs: List[Job]
    total: int
    total_pages: int = 0
    next_start_page: int = 0
    has_more: bool = False

class CandidateSubmission(BaseModel):
    candidate_name: str
    email: str
    phone: Optional[str] = None
    job_id: str

# Auth Pydantic Models
class UserCreate(BaseModel):
    email: str
    full_name: str
    password: str

class UserLogin(BaseModel):
    email: str
    password: str

class UserResponse(BaseModel):
    id: str
    email: str
    full_name: str
    is_active: str
    created_at: datetime

class Token(BaseModel):
    access_token: str
    token_type: str
    user: UserResponse


class TokenData(BaseModel):
    email: Optional[str] = None

# Auth Utility Functions - now using bcrypt directly (defined above)

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

async def get_current_user(token: str = Depends(HTTPBearer())):
    """Get current user from JSON storage"""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token.credentials, SECRET_KEY, algorithms=[ALGORITHM])
        email: str = payload.get("sub")
        if email is None:
            raise credentials_exception
        token_data = TokenData(email=email)
    except JWTError:
        raise credentials_exception
    
    # Load fresh from JSON to ensure we have latest data
    users = load_users_from_json()
    user = users.get(token_data.email.lower())
    
    if user is None:
        raise credentials_exception
    if user.get("is_active") != "true":
        raise HTTPException(status_code=400, detail="Inactive user")
    
    # Return as dict-like object for compatibility
    return type('User', (), {
        'id': user["id"],
        'email': user["email"],
        'full_name': user["full_name"],
        'is_active': user["is_active"],
        'hashed_password': user["hashed_password"]
    })()

# Auth Endpoints
@app.post("/api/auth/register", response_model=Token)
async def register(user_data: UserCreate):
    """Register a new user - only whitelisted emails allowed (JSON storage)"""
    global _users_cache
    
    # Check if email is in whitelist
    if user_data.email.lower() not in WHITELISTED_USERS:
        raise HTTPException(status_code=403, detail="Email not authorized. Contact admin for access.")
    
    # Check if user already exists
    if user_data.email.lower() in _users_cache:
        raise HTTPException(status_code=400, detail="Email already registered")
    
    # Create new user
    user_id = str(uuid4())
    hashed_password = get_password_hash(user_data.password)
    
    user = {
        "id": user_id,
        "email": user_data.email,
        "full_name": user_data.full_name,
        "hashed_password": hashed_password,
        "is_active": "true",
        "created_at": datetime.now().isoformat()
    }
    
    # Save to JSON file
    _users_cache[user_data.email.lower()] = user
    save_users_to_json(_users_cache)
    
    # Create access token
    access_token = create_access_token(data={"sub": user["email"]})
    
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": {
            "id": user["id"],
            "email": user["email"],
            "full_name": user["full_name"],
            "is_active": user["is_active"],
            "created_at": user["created_at"]
        }
    }

@app.post("/api/auth/login", response_model=Token)
async def login(user_data: UserLogin):
    """Login user and return JWT token - auto-creates whitelisted users on first login (JSON storage)"""
    global _users_cache
    
    email_lower = user_data.email.lower()
    user = _users_cache.get(email_lower)
    
    # If user doesn't exist, check if email is whitelisted and auto-create
    if not user:
        if email_lower not in WHITELISTED_USERS:
            raise HTTPException(status_code=401, detail="Incorrect email or password")
        
        # Auto-create user on first login (whitelisted email)
        print(f"[Auth] Auto-creating new user: {user_data.email}")
        user_id = str(uuid4())
        hashed_password = get_password_hash(user_data.password)
        
        user = {
            "id": user_id,
            "email": user_data.email,
            "full_name": user_data.email.split('@')[0],
            "hashed_password": hashed_password,
            "is_active": "true",
            "created_at": datetime.now().isoformat()
        }
        
        # Save to JSON file for persistence
        _users_cache[email_lower] = user
        save_users_to_json(_users_cache)
        print(f"[Auth] User created successfully: {user_data.email}")
    
    if not verify_password(user_data.password, user["hashed_password"]):
        raise HTTPException(status_code=401, detail="Incorrect email or password")
    
    if user["is_active"] != "true":
        raise HTTPException(status_code=400, detail="Inactive user")
    
    # Create access token
    access_token = create_access_token(data={"sub": user["email"]})
    
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": {
            "id": user["id"],
            "email": user["email"],
            "full_name": user["full_name"],
            "is_active": user["is_active"],
            "created_at": user["created_at"]
        }
    }

@app.post("/api/auth/reset-password")
async def reset_password(user_data: UserLogin):
    """Reset password for existing user (whitelisted emails only)"""
    global _users_cache
    
    email_lower = user_data.email.lower()
    
    # Check if email is whitelisted
    if email_lower not in WHITELISTED_USERS:
        raise HTTPException(status_code=403, detail="Email not authorized")
    
    # Check if user exists
    if email_lower not in _users_cache:
        raise HTTPException(status_code=404, detail="User not found. Please login first to create your account.")
    
    # Update password
    hashed_password = get_password_hash(user_data.password)
    _users_cache[email_lower]["hashed_password"] = hashed_password
    
    # Save to JSON file
    save_users_to_json(_users_cache)
    
    return {"message": "Password reset successfully"}

@app.get("/api/admin/users")
async def get_whitelisted_users(current_user: UserDB = Depends(get_current_user)):
    """Get list of whitelisted users (admin only)"""
    is_admin = current_user.email.lower() == ADMIN_EMAIL.lower()
    if not is_admin:
        raise HTTPException(status_code=403, detail="Only admin can manage users")
    
    return {
        "users": sorted(list(WHITELISTED_USERS)),
        "count": len(WHITELISTED_USERS)
    }

@app.post("/api/admin/users")
async def add_whitelisted_user(
    email: str = Form(...),
    current_user: UserDB = Depends(get_current_user)
):
    """Add a new user to whitelist (admin only)"""
    global WHITELISTED_USERS
    
    is_admin = current_user.email.lower() == ADMIN_EMAIL.lower()
    if not is_admin:
        raise HTTPException(status_code=403, detail="Only admin can add users")
    
    email_lower = email.lower().strip()
    
    # Validate email format
    if not email_lower or "@" not in email_lower:
        raise HTTPException(status_code=400, detail="Invalid email format")
    
    # Check if already exists
    if email_lower in WHITELISTED_USERS:
        raise HTTPException(status_code=400, detail="User already whitelisted")
    
    # Add to whitelist
    WHITELISTED_USERS.add(email_lower)
    
    # Save to file
    if save_whitelisted_users():
        return {"message": f"User {email} added to whitelist", "email": email_lower}
    else:
        raise HTTPException(status_code=500, detail="Failed to save users file")

@app.delete("/api/admin/users/{email}")
async def remove_whitelisted_user(
    email: str,
    current_user: UserDB = Depends(get_current_user)
):
    """Remove a user from whitelist (admin only)"""
    global WHITELISTED_USERS
    
    is_admin = current_user.email.lower() == ADMIN_EMAIL.lower()
    if not is_admin:
        raise HTTPException(status_code=403, detail="Only admin can remove users")
    
    email_lower = email.lower().strip()
    
    # Cannot remove admin
    if email_lower == ADMIN_EMAIL.lower():
        raise HTTPException(status_code=400, detail="Cannot remove admin user")
    
    # Check if exists
    if email_lower not in WHITELISTED_USERS:
        raise HTTPException(status_code=404, detail="User not found in whitelist")
    
    # Remove from whitelist
    WHITELISTED_USERS.remove(email_lower)
    
    # Save to file
    if save_whitelisted_users():
        return {"message": f"User {email} removed from whitelist", "email": email_lower}
    else:
        raise HTTPException(status_code=500, detail="Failed to save users file")

@app.get("/api/auth/me", response_model=UserResponse)
async def get_current_user_info(current_user: UserDB = Depends(get_current_user)):
    """Get current logged in user info"""
    return {
        "id": current_user.id,
        "email": current_user.email,
        "full_name": current_user.full_name,
        "is_active": current_user.is_active,
        "created_at": current_user.created_at
    }

# Admin configuration
ADMIN_EMAIL = "Admin@radixsol.com"

# Client names to filter from job descriptions (hidden from vendors)
CLIENT_NAMES_TO_FILTER = [
    "Adaptive", "AHSA", "CareerStaff", "HWL", "Medefis", "Staffing Engine",
    "Aya", "Dedicated Nurses", "Hallmark and Vibra Healthcare", "Focusoneconnect",
    "RTG Medical", "Stability Healthcare", "Sunburst Workforce Solutions",
    "Supplemental Healthcare", "TRS Healthcare", "Windsor", "Expedient",
    "Snapcare", "MedicalSolutions", "OHT", "Gracedale"
]

def sanitize_job_description(description: str, is_admin: bool = False) -> str:
    """Remove client names from job description for non-admin users"""
    if is_admin or not description:
        return description
    
    sanitized = description
    for client_name in CLIENT_NAMES_TO_FILTER:
        # Case-insensitive replacement with word boundaries
        import re
        pattern = r'\b' + re.escape(client_name) + r'\b'
        sanitized = re.sub(pattern, '[Client Name Hidden]', sanitized, flags=re.IGNORECASE)
    
    return sanitized

if os.path.isdir(UPLOAD_DIR):
    app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")

# Ceipal API Integration
class CeipalClient:
    def __init__(self):
        self.auth_url = CEIPAL_AUTH_URL
        self.reports_url = CEIPAL_REPORTS_URL
        self.email = CEIPAL_EMAIL
        self.password = CEIPAL_PASSWORD
        self.api_key = CEIPAL_API_KEY
        self.auth_token = None
        self.token_expires = None
        self.cache_dir = CEIPAL_CACHE_DIR
        self.last_auth_error = None
        self._jobs_cache = None
        self._jobs_cache_time = None
        self._fetch_lock = asyncio.Lock()  # Prevent concurrent fetches
        self._last_fetched_pages = 0  # Track how many pages were fetched
        os.makedirs(self.cache_dir, exist_ok=True)

    def _get_cached_jobs(self) -> Optional[List[Job]]:
        """Return cached jobs if less than 5 minutes old"""
        if self._jobs_cache and self._jobs_cache_time:
            age = datetime.now() - self._jobs_cache_time
            if age < timedelta(minutes=5):
                return self._jobs_cache
        return None

    def _set_cached_jobs(self, jobs: List[Job]):
        """Cache jobs with timestamp"""
        self._jobs_cache = jobs
        self._jobs_cache_time = datetime.now()
    
    def clear_cache(self):
        """Clear the jobs cache to force fresh fetch"""
        self._jobs_cache = None
        self._jobs_cache_time = None

    def _cache_path(self, filename: str) -> str:
        return os.path.join(self.cache_dir, filename)

    def _write_json_cache(self, filename: str, payload) -> None:
        try:
            with open(self._cache_path(filename), "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
        except Exception as e:
            if DEBUG:
                print(f"Failed to write cache {filename}: {e}")

    def _read_json_cache(self, filename: str):
        try:
            with open(self._cache_path(filename), "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    def _extract_authtoken(self, auth_result: dict) -> Optional[str]:
        if not isinstance(auth_result, dict):
            return None
        raw = auth_result.get("_raw")
        if isinstance(raw, str) and raw.strip():
            token = self._extract_token_from_raw(raw)
            if token:
                return token
        # Common shapes we’ve seen in similar APIs
        for key in ("authtoken", "authToken", "token", "access_token", "accessToken"):
            if auth_result.get(key):
                return auth_result.get(key)
        data = auth_result.get("data")
        if isinstance(data, dict):
            for key in ("authtoken", "authToken", "token", "access_token", "accessToken"):
                if data.get(key):
                    return data.get(key)
        return None

    def _extract_token_from_raw(self, raw: str) -> Optional[str]:
        """Ceipal may return XML even when json=1 is sent."""
        try:
            # Example:
            # <root><access_token>...</access_token><refresh_token>...</refresh_token></root>
            m = re.search(r"<access_token>([^<]+)</access_token>", raw)
            if m:
                return m.group(1).strip()
            # Fallback for other token tags
            m = re.search(r"<authtoken>([^<]+)</authtoken>", raw)
            if m:
                return m.group(1).strip()
        except Exception:
            return None
        return None
    
    async def authenticate(self) -> bool:
        """Authenticate with Ceipal API and get auth token"""
        try:
            async with httpx.AsyncClient() as client:
                auth_data = {
                    "email": self.email,
                    "password": self.password,
                    "api_key": self.api_key,
                    "json": 1
                }

                # Do not print secrets; store raw responses to cache for inspection.
                self.last_auth_error = None

                # Attempt 1: JSON body (many APIs expect this)
                response = await client.post(
                    self.auth_url,
                    json=auth_data,
                    headers={"Content-Type": "application/json"}
                )

                # If API expects form-encoded it may return 4xx; retry with form.
                if response.status_code >= 400:
                    response = await client.post(
                        self.auth_url,
                        data=auth_data,
                        headers={"Content-Type": "application/x-www-form-urlencoded"}
                    )

                auth_text = response.text
                auth_json = None
                try:
                    auth_json = response.json()
                except Exception:
                    auth_json = {"_raw": auth_text}

                self._write_json_cache(
                    "ceipal_auth_last.json",
                    {
                        "timestamp": datetime.now().isoformat(),
                        "status_code": response.status_code,
                        "url": self.auth_url,
                        "response": auth_json,
                    },
                )

                if response.status_code >= 400:
                    self.last_auth_error = f"HTTP {response.status_code}"
                    return False

                token = self._extract_authtoken(auth_json if isinstance(auth_json, dict) else {})
                if token:
                    self.auth_token = token
                    self.token_expires = datetime.now() + timedelta(hours=24)
                    return True

                # Some APIs signal success differently; keep raw response cached.
                self.last_auth_error = "Token not found in response"
                return False
                    
        except httpx.HTTPError as e:
            self.last_auth_error = str(e)
            if DEBUG:
                print(f"Authentication HTTP error: {e}")
            return False
        except Exception as e:
            self.last_auth_error = str(e)
            if DEBUG:
                print(f"Unexpected authentication error: {e}")
            return False
    
    async def get_auth_token(self) -> str:
        """Get valid auth token, refresh if needed"""
        if not self.auth_token or (self.token_expires and datetime.now() >= self.token_expires):
            if not await self.authenticate():
                raise HTTPException(status_code=401, detail="Failed to authenticate with Ceipal API")
        return self.auth_token
    
    async def fetch_jobs(self) -> List[Job]:
        """Fetch all jobs from Ceipal Reports API with pagination support and caching"""
        # Check cache first (outside lock for performance)
        cached_jobs = self._get_cached_jobs()
        if cached_jobs:
            return cached_jobs
        
        # Use lock to prevent multiple concurrent fetches
        async with self._fetch_lock:
            # Double-check cache after acquiring lock
            cached_jobs = self._get_cached_jobs()
            if cached_jobs:
                return cached_jobs
            
            all_jobs: List[Job] = []
            
            try:
                token = await self.get_auth_token()
                
                async with httpx.AsyncClient() as client:
                    headers = {
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json"
                    }
                    
                    page = 1
                    has_next = True
                    total_records = 0
                    
                    consecutive_429_errors = 0
                    max_429_retries = 3
                    
                    while has_next:  # Fetch ALL pages until has_next is false
                        # Fetch current page
                        url = f"{self.reports_url}?response_type=1&page={page}"
                        print(f"[Ceipal] Fetching page {page}...")
                        
                        try:
                            response = await client.get(url, headers=headers, timeout=60.0)
                            response.raise_for_status()
                            consecutive_429_errors = 0  # Reset on success
                        except httpx.HTTPStatusError as e:
                            if e.response.status_code == 429:
                                consecutive_429_errors += 1
                                if consecutive_429_errors > max_429_retries:
                                    print(f"[Ceipal] Too many 429 errors, stopping at page {page}. Got {len(all_jobs)} jobs.")
                                    has_next = False
                                    break
                                
                                # Exponential backoff
                                wait_time = min(2 ** consecutive_429_errors, 30)
                                print(f"[Ceipal] Rate limited (429). Waiting {wait_time}s...")
                                await asyncio.sleep(wait_time)
                                continue  # Retry same page
                            else:
                                raise  # Re-raise other errors
                        
                        reports_data = response.json()
                        
                        # Get total records from first page
                        if page == 1:
                            total_records = int(reports_data.get("record_count", 0))
                            print(f"[Ceipal] Total records available: {total_records}")
                            self._write_json_cache(
                                "ceipal_reports_last.json",
                                {
                                    "timestamp": datetime.now().isoformat(),
                                    "status_code": response.status_code,
                                    "url": url,
                                    "response": reports_data,
                                },
                            )
                        
                        # Parse jobs from this page
                        page_jobs = await self._parse_jobs_from_reports(reports_data)
                        all_jobs.extend(page_jobs)
                        print(f"[Ceipal] Page {page}: fetched {len(page_jobs)} jobs, total so far: {len(all_jobs)}")
                        
                        # Check if there's a next page
                        has_next_page_val = reports_data.get("has_next_page")
                        next_page_val = reports_data.get("next_page")
                        has_next = bool(has_next_page_val) or bool(next_page_val)
                        
                        print(f"[Ceipal] has_next_page={has_next_page_val}, next_page exists={bool(next_page_val)}, has_next={has_next}")
                        
                        # Stop if we have all records
                        if len(all_jobs) >= total_records and total_records > 0:
                            print(f"[Ceipal] Got all {total_records} records, stopping pagination")
                            has_next = False
                        
                        page += 1
                    
                    print(f"[Ceipal] Finished fetching {len(all_jobs)} jobs from {page-1} pages")
                    
                    # Track how many pages we fetched for pagination info
                    self._last_fetched_pages = page - 1
                    self._last_total_records = total_records  # Store total available from Ceipal
                    
                    # Cache the results
                    self._set_cached_jobs(all_jobs)
                    return all_jobs
                    
            except httpx.HTTPError as e:
                if DEBUG:
                    print(f"Ceipal API Error: {e}")
            except Exception as e:
                if DEBUG:
                    print(f"Error fetching jobs: {e}")
            
            # Fallback: try cached reports
            cached = self._read_json_cache("ceipal_reports_last.json")
            if isinstance(cached, dict) and isinstance(cached.get("response"), (dict, list)):
                try:
                    cached_data = cached.get("response")
                    return await self._parse_jobs_from_reports(cached_data)
                except Exception:
                    pass
            
            # Last resort: mock jobs
            return self._get_mock_jobs()
    
    async def fetch_all_jobs_background(self):
        """Background task to fetch all jobs progressively and update cache"""
        print("[Background] Starting progressive job fetch...")
        all_jobs = []
        consecutive_429_errors = 0
        max_429_retries = 5
        
        try:
            token = await self.get_auth_token()
            
            async with httpx.AsyncClient() as client:
                headers = {
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json"
                }
                
                page = 1
                has_next = True
                total_records = 0
                
                while has_next:
                    url = f"{self.reports_url}?response_type=1&page={page}"
                    print(f"[Background] Fetching page {page}...")
                    
                    try:
                        response = await client.get(url, headers=headers, timeout=60.0)
                        response.raise_for_status()
                        consecutive_429_errors = 0  # Reset on success
                        
                    except httpx.HTTPStatusError as e:
                        if e.response.status_code == 429:
                            consecutive_429_errors += 1
                            if consecutive_429_errors > max_429_retries:
                                print(f"[Background] Too many 429 errors, stopping. Got {len(all_jobs)} jobs.")
                                break
                            
                            # Exponential backoff: 2^errors seconds (2, 4, 8, 16, 32...)
                            wait_time = min(2 ** consecutive_429_errors, 60)
                            print(f"[Background] Rate limited (429). Waiting {wait_time}s before retry...")
                            await asyncio.sleep(wait_time)
                            continue  # Retry same page
                        else:
                            raise  # Re-raise other errors
                    
                    reports_data = response.json()
                    
                    if page == 1:
                        total_records = int(reports_data.get("record_count", 0))
                        print(f"[Background] Total records available: {total_records}")
                    
                    # Parse jobs from this page
                    page_jobs = await self._parse_jobs_from_reports(reports_data)
                    all_jobs.extend(page_jobs)
                    print(f"[Background] Page {page}: fetched {len(page_jobs)} jobs, total so far: {len(all_jobs)}")
                    
                    # Update cache progressively every 5 pages or when done
                    if page % 5 == 0 or not has_next:
                        self._set_cached_jobs(all_jobs)
                        self._last_fetched_pages = page
                        self._last_total_records = total_records
                        print(f"[Background] Cache updated with {len(all_jobs)} jobs")
                    
                    # Check if there's a next page
                    has_next_page_val = reports_data.get("has_next_page")
                    next_page_val = reports_data.get("next_page")
                    has_next = (
                        (has_next_page_val == 1 or has_next_page_val == "1" or has_next_page_val is True) or
                        (next_page_val is not None and int(next_page_val) > page)
                    )
                    
                    if has_next:
                        page += 1
                        # Longer delay to avoid rate limiting (2 seconds between requests)
                        await asyncio.sleep(2.0)
                
                # Final cache update
                self._set_cached_jobs(all_jobs)
                self._last_fetched_pages = page - 1
                self._last_total_records = total_records
                print(f"[Background] Completed fetching {len(all_jobs)} jobs from {page-1} pages")
                
        except Exception as e:
            print(f"[Background] Error fetching jobs: {e}")
            # Save whatever we got
            if all_jobs:
                self._set_cached_jobs(all_jobs)
    
    async def fetch_more_jobs(self, start_page: int, max_pages: int = 25) -> List[Job]:
        """Fetch additional pages of jobs beyond initial load"""
        more_jobs: List[Job] = []
        consecutive_429_errors = 0
        max_429_retries = 3
        
        try:
            token = await self.get_auth_token()
            
            async with httpx.AsyncClient() as client:
                headers = {
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json"
                }
                
                page = start_page
                end_page = start_page + max_pages - 1
                has_next = True
                
                while has_next and page <= end_page:
                    url = f"{self.reports_url}?response_type=1&page={page}"
                    print(f"[Ceipal] Loading more - page {page}...")
                    
                    try:
                        response = await client.get(url, headers=headers, timeout=60.0)
                        response.raise_for_status()
                        consecutive_429_errors = 0  # Reset on success
                    except httpx.HTTPStatusError as e:
                        if e.response.status_code == 429:
                            consecutive_429_errors += 1
                            if consecutive_429_errors > max_429_retries:
                                print(f"[Ceipal] Too many 429 errors, stopping. Got {len(more_jobs)} jobs.")
                                break
                            
                            # Exponential backoff
                            wait_time = min(2 ** consecutive_429_errors, 30)
                            print(f"[Ceipal] Rate limited (429). Waiting {wait_time}s...")
                            await asyncio.sleep(wait_time)
                            continue  # Retry same page
                        else:
                            raise
                    
                    reports_data = response.json()
                    page_jobs = await self._parse_jobs_from_reports(reports_data)
                    more_jobs.extend(page_jobs)
                    
                    has_next_page_val = reports_data.get("has_next_page")
                    next_page_val = reports_data.get("next_page")
                    has_next = bool(has_next_page_val) or bool(next_page_val)
                    
                    page += 1
                
                print(f"[Ceipal] Loaded {len(more_jobs)} more jobs from pages {start_page}-{page-1}")
                return more_jobs
                
        except Exception as e:
            print(f"[Ceipal] Error loading more jobs: {e}")
            return []

    async def _parse_jobs_from_reports(self, reports_data) -> List[Job]:
        """Parse Ceipal reports data into Job models.
        
        Ceipal response structure:
        {
            "success": 1,
            "message": "Records Found",
            "record_count": "35625",
            "result": [
                {
                    "JobCode": "JPC - 267008",
                    "JobTitle": "Registered Nurse - PACU",
                    "JobStatus": "Open",  # or "Active"
                    "States": "New Mexico",
                    "Location": "[Albuquerque, NM, 87106]",
                    "Client": "Aya Healthcare",
                    "EndClient": "University of New Mexico Hospital",
                    "Duration": "13Weeks",
                    "ClientBillRateSalary": "USD/76",
                    ...
                }
            ]
        }
        """
        jobs: List[Job] = []
        
        if not isinstance(reports_data, dict):
            return jobs
            
        # Get the result array from Ceipal response
        job_data_list = reports_data.get("result", [])
        if not job_data_list:
            # Try other common keys
            job_data_list = reports_data.get("data", reports_data.get("jobs", reports_data.get("records", [])))
        
        if not isinstance(job_data_list, list):
            return jobs
        
        for job_data in job_data_list:
            if not isinstance(job_data, dict):
                continue
                
            # Only include jobs with status "Open" or "Active"
            job_status = job_data.get("JobStatus", "").lower()
            if job_status not in ["open", "active"]:
                continue
            
            # Parse location from Ceipal format: "[City, State, ZIP]" or just use States
            location_raw = job_data.get("Location", "")
            states = job_data.get("States", "")
            location = states
            if location_raw and location_raw != "N/A":
                # Clean up location format: "[City, State, ZIP]" -> "City, State"
                location_clean = location_raw.strip("[]")
                location = location_clean
            
            # Get the actual job description from Ceipal
            job_description = job_data.get("JobDescription", "").strip()
            requirements = job_data.get("Requirements", "").strip() or job_data.get("JobRequirements", "").strip()
            
            # Combine description and requirements for full details
            full_description_parts = []
            if job_description:
                full_description_parts.append(job_description)
            if requirements:
                full_description_parts.append(f"Requirements:\n{requirements}")
            
            if full_description_parts:
                description = "\n\n".join(full_description_parts)
            else:
                # Build description from available fields as fallback
                description_parts = []
                end_client = job_data.get("EndClient", "")
                duration = job_data.get("Duration", "")
                if end_client:
                    description_parts.append(f"End Client: {end_client}")
                if duration:
                    description_parts.append(f"Duration: {duration}")
                description = " | ".join(description_parts) if description_parts else job_data.get("JobTitle", "")
            
            # MSP Fee mapping (actual fee + 1% as requested)
            msp_fees = {
                "AHSA": 7.25,
                "Triage/RTG": 6.50,
                "PTH": 6.0,
                "Supplemental": 8.0,
                "Aya": 4.0,
                "Aya Healthcare": 4.0,
                "HWL": 7.50,
                "Medical Solutions": 7.0,
                "Careerstaff": 7.50,
                "DNA": 7.25,
                "Stability": 6.50,
                "Snapcare": 6.0,
                "Adaptive": 7.75,
                "Sunburst": 6.0,
                "Staffing Engine": 7.0,
                "Medefis": 7.25,
                "Windsor": 6.0,
                "WAE (Gracedale Nursing Home)": 6.0,
                "Hallmark and Vibra Healthcare": 7.0,
                "Expedient": 6.0,
                "TRS": 7.0,
                "Favorite Healthcare": 8.0,
                "OHT": 6.0,
            }
            
            # Get client name and calculate updated bill rate
            client_name = job_data.get("Client", "")
            actual_bill_rate_str = job_data.get("ClientBillRateSalary", job_data.get("BillRate", "0"))
            
            # Parse actual bill rate (handle formats like "USD/76" or "76")
            actual_rate = 0.0
            try:
                # Extract numeric value from string
                rate_match = re.search(r'[\d.]+', str(actual_bill_rate_str))
                if rate_match:
                    actual_rate = float(rate_match.group())
            except:
                actual_rate = 0.0
            
            # Calculate updated bill rate: subtract (MSP fee + 1%) from actual rate
            total_fee_percent = msp_fees.get(client_name, 7.0)  # Default 7% if client not found
            updated_rate = actual_rate - ((total_fee_percent * actual_rate) / 100)
            
            # Format salary range with updated rate (hide actual)
            if updated_rate > 0:
                salary_range_display = f"${updated_rate:.2f}/hr"
            else:
                salary_range_display = "Contact for rate"
            
            # Get actual job code
            actual_job_code = str(job_data.get("JobCode", f"job_{len(jobs)+1}"))
            
            # Extract End Client from Ceipal
            end_client = job_data.get("EndClient", "")
            
            # Map Ceipal fields to our Job model
            job = Job(
                id=actual_job_code,
                title=job_data.get("JobTitle", "Position Not Specified"),
                description=description,
                requirements=requirements,
                department=f"Job Code: {actual_job_code}",
                location=location if location else "Not specified",
                employment_type=job_data.get("Duration", "Contract"),  # Duration as employment type
                salary_range=salary_range_display,  # Show updated rate to vendors
                posted_date=self._parse_date(job_data.get("JobCreated", job_data.get("CreatedDate"))),
                status=job_data.get("JobStatus", "Open"),
                end_client=end_client if end_client else None
            )
            jobs.append(job)
            
        return jobs
    
    def _parse_date(self, date_str: str) -> datetime:
        """Parse date string from various formats"""
        if not date_str:
            return datetime.now()
        
        try:
            # Try different date formats
            for fmt in ["%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%d-%m-%Y", "%m/%d/%Y", "%Y%m%d"]:
                try:
                    return datetime.strptime(date_str, fmt)
                except ValueError:
                    continue
            
            # If all formats fail, return current date
            return datetime.now()
        except Exception:
            return datetime.now()
    
    def _get_mock_jobs(self) -> List[Job]:
        """Mock data for demonstration when Ceipal API is not available"""
        return [
            Job(
                id="ceipal_001",
                title="Senior Software Developer",
                description="Looking for an experienced Software Developer to join our development team.",
                department="Engineering",
                location="New York, NY",
                employment_type="Full-time",
                salary_range="$100,000 - $140,000",
                posted_date=datetime.now(),
                status="active",
                requirements="5+ years of software development experience, strong knowledge of modern frameworks."
            ),
            Job(
                id="ceipal_002",
                title="Business Analyst",
                description="Seeking a Business Analyst to analyze business requirements and create solutions.",
                department="Business Analysis",
                location="Remote",
                employment_type="Contract",
                salary_range="$80,000 - $100,000",
                posted_date=datetime.now(),
                status="active",
                requirements="3+ years of business analysis experience, excellent communication skills."
            ),
            Job(
                id="ceipal_003",
                title="Project Manager",
                description="Experienced Project Manager needed to oversee multiple projects and teams.",
                department="Project Management",
                location="Chicago, IL",
                employment_type="Full-time",
                salary_range="$110,000 - $150,000",
                posted_date=datetime.now(),
                status="active",
                requirements="PMP certification preferred, 5+ years of project management experience."
            )
        ]

ceipal_client = CeipalClient()

# API Endpoints
@app.get("/")
async def root():
    return {"message": "VMS Backend API is running"}

@app.get("/api/jobs", response_model=JobListResponse)
async def get_jobs(background_tasks: BackgroundTasks, current_user: UserDB = Depends(get_current_user)):
    """Get all active jobs from Ceipal API (uses cache if available for fast response)"""
    try:
        # Always return cached jobs immediately for fast response
        cached_jobs = ceipal_client._get_cached_jobs() or ceipal_client._jobs_cache or []
        
        # Trigger background refresh if cache is empty or older than 5 minutes
        cache_age = datetime.now() - (ceipal_client._jobs_cache_time or datetime.min)
        if not cached_jobs or cache_age > timedelta(minutes=5):
            # Trigger background fetch without waiting (progressive loading)
            background_tasks.add_task(ceipal_client.fetch_all_jobs_background)
            print(f"[API] Triggered background job fetch. Current cache: {len(cached_jobs)} jobs")
        
        # Calculate pagination info
        total_pages = ceipal_client._last_fetched_pages if hasattr(ceipal_client, '_last_fetched_pages') else 0
        total_records = getattr(ceipal_client, '_last_total_records', 0)
        jobs_fetched = len(cached_jobs)
        
        # Has more if we fetched less than total records available
        has_more = (jobs_fetched < total_records and total_records > 0) or total_pages == 0
        
        # Check if user is admin for description sanitization
        is_admin = current_user.email.lower() == ADMIN_EMAIL.lower()
        
        # Sanitize job descriptions for non-admin users (vendors)
        sanitized_jobs = []
        for job in cached_jobs:
            job_dict = job.dict() if hasattr(job, 'dict') else job
            if not is_admin and 'description' in job_dict:
                job_dict['description'] = sanitize_job_description(job_dict['description'], is_admin)
            sanitized_jobs.append(job_dict)
        
        return JobListResponse(
            jobs=sanitized_jobs, 
            total=len(sanitized_jobs),
            total_pages=total_pages,
            next_start_page=total_pages + 1,
            has_more=has_more
        )
    except Exception as e:
        # If fetch fails, try to return any cached data as fallback
        if ceipal_client._jobs_cache:
            total_pages = ceipal_client._last_fetched_pages if hasattr(ceipal_client, '_last_fetched_pages') else 25
            # Check if user is admin for fallback too
            is_admin = current_user.email.lower() == ADMIN_EMAIL.lower()
            sanitized_jobs = []
            for job in ceipal_client._jobs_cache:
                job_dict = job.dict() if hasattr(job, 'dict') else job
                if not is_admin and 'description' in job_dict:
                    job_dict['description'] = sanitize_job_description(job_dict['description'], is_admin)
                sanitized_jobs.append(job_dict)
            return JobListResponse(
                jobs=sanitized_jobs, 
                total=len(sanitized_jobs),
                total_pages=total_pages,
                next_start_page=total_pages + 1,
                has_more=total_pages >= 25
            )
        raise HTTPException(status_code=500, detail=f"Failed to fetch jobs: {str(e)}")

@app.get("/api/jobs/{job_id}", response_model=Job)
async def get_job(job_id: str):
    """Get specific job details"""
    try:
        # Try cache first
        jobs = ceipal_client._get_cached_jobs()
        if not jobs:
            # No cache, fetch fresh
            jobs = await ceipal_client.fetch_jobs()
        
        job = next((job for job in jobs if job.id == job_id), None)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        return job
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch job: {str(e)}")

@app.get("/api/jobs/load-more")
async def load_more_jobs(start_page: int = 26, max_pages: int = 25):
    """Load additional pages of jobs for infinite scroll"""
    try:
        more_jobs = await ceipal_client.fetch_more_jobs(start_page, max_pages)
        return {"jobs": more_jobs, "total": len(more_jobs), "start_page": start_page}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load more jobs: {str(e)}")

@app.get("/api/ceipal/test")
async def test_ceipal_connection():
    """Test Ceipal API connection and authentication"""
    try:
        auth_success = await ceipal_client.authenticate()
        if auth_success:
            return {
                "status": "success",
                "message": "Ceipal API authentication successful",
                "auth_token": ceipal_client.auth_token[:20] + "..." if ceipal_client.auth_token else None,
                "token_expires": ceipal_client.token_expires.isoformat() if ceipal_client.token_expires else None
            }
        else:
            return {
                "status": "error",
                "message": "Ceipal API authentication failed",
                "detail": ceipal_client.last_auth_error
            }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ceipal API test failed: {str(e)}")

@app.get("/api/ceipal/cache")
async def get_ceipal_cache_status():
    """Get metadata about the last cached Ceipal auth and reports responses."""
    auth_cached = ceipal_client._read_json_cache("ceipal_auth_last.json")
    reports_cached = ceipal_client._read_json_cache("ceipal_reports_last.json")
    return {
        "auth_cached": auth_cached,
        "reports_cached": reports_cached,
    }

@app.get("/api/health")
async def health_check(db: Session = Depends(get_db)):
    """Health check endpoint to verify database persistence"""
    import os
    
    # Get database file path
    db_path = DATABASE_URL.replace("sqlite://", "").lstrip("/")
    if not db_path.startswith("/"):
        db_path = "/" + db_path
    
    # Check if database file exists
    db_exists = os.path.exists(db_path)
    db_size = os.path.getsize(db_path) if db_exists else 0
    
    # Get user count
    try:
        user_count = db.query(UserDB).count()
    except:
        user_count = 0
    
    return {
        "database_path": db_path,
        "database_exists": db_exists,
        "database_size_bytes": db_size,
        "user_count": user_count,
        "upload_dir": UPLOAD_DIR,
        "upload_dir_exists": os.path.exists(UPLOAD_DIR),
    }

@app.post("/api/ceipal/refresh")
async def force_refresh_jobs():
    """Clear cache and force fresh job fetch from Ceipal"""
    try:
        ceipal_client.clear_cache()
        jobs = await ceipal_client.fetch_jobs()
        
        # Read the cached first page to get pagination info
        cache_data = ceipal_client._read_json_cache("ceipal_reports_last.json")
        pagination_info = {}
        if cache_data and cache_data.get("response"):
            resp = cache_data["response"]
            pagination_info = {
                "record_count": resp.get("record_count"),
                "page_count": resp.get("page_count"),
                "limit": resp.get("limit"),
                "has_next_page": resp.get("has_next_page"),
                "has_prev_page": resp.get("has_prev_page"),
                "next_page_exists": bool(resp.get("next_page")),
            }
        
        return {
            "message": "Jobs refreshed successfully",
            "jobs_fetched": len(jobs),
            "cached": False,
            "pagination_from_page1": pagination_info
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to refresh jobs: {str(e)}")

@app.get("/api/ceipal/reports")
async def get_ceipal_reports():
    """Get raw reports data from Ceipal API"""
    try:
        token = await ceipal_client.get_auth_token()
        
        async with httpx.AsyncClient() as client:
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json"
            }
            
            response = await client.get(
                f"{ceipal_client.reports_url}?response_type=1",
                headers=headers
            )
            response.raise_for_status()
            
            return {
                "status": "success",
                "data": response.json()
            }
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch Ceipal reports: {str(e)}")

@app.post("/api/candidates/submit")
async def submit_candidate(
    candidate_name: str = Form(...),
    email: str = Form(...), 
    phone: str = Form(...),
    bill_rate: str = Form(...),
    current_location: str = Form(...),
    primary_skills: str = Form(...),
    job_title: str = Form(...),
    years_experience: str = Form(...),
    tentative_start_date: str = Form(...),
    rto: str = Form(...),
    candidate_summary: str = Form(...),
    job_id: str = Form(...),
    resume: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    """Submit candidate resume for a job (requires authentication)"""
    try:
        # Validate file
        if resume.size > MAX_FILE_SIZE:
            raise HTTPException(status_code=413, detail="File too large")
        
        file_extension = resume.filename.split(".")[-1].lower()
        allowed_extensions = ["pdf", "doc", "docx", "txt"]
        if file_extension not in allowed_extensions:
            raise HTTPException(status_code=400, detail="Invalid file type")
        
        # Save resume
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{candidate_name.replace(' ', '_')}_{timestamp}.{file_extension}"
        file_path = os.path.join(UPLOAD_DIR, filename)
        
        async with aiofiles.open(file_path, 'wb') as f:
            content = await resume.read()
            await f.write(content)
        
        # Generate unique candidate ID
        count = db.query(CandidateDB).count()
        candidate_id = f"candidate_{count + 1}"
        
        # Store candidate in database with submitter info
        db_candidate = CandidateDB(
            id=candidate_id,
            name=candidate_name,
            email=email,
            phone=phone,
            job_id=job_id,
            resume_path=file_path,
            submitted_date=datetime.now(),
            status="submitted",
            submitted_by_user_id=current_user.id,  # Track who submitted
            bill_rate=bill_rate,
            current_location=current_location,
            primary_skills=primary_skills,
            job_title=job_title,
            years_experience=years_experience,
            tentative_start_date=tentative_start_date,
            rto=rto,
            candidate_summary=candidate_summary
        )
        db.add(db_candidate)
        db.commit()
        db.refresh(db_candidate)
        
        return {
            "message": "Candidate submitted successfully",
            "candidate_id": candidate_id,
            "status": "submitted",
            "submitted_by": current_user.full_name
        }
        
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to submit candidate: {str(e)}")

@app.get("/api/candidates/job/{job_id}")
async def get_candidates_for_job(job_id: str, db: Session = Depends(get_db)):
    """Get all candidates submitted for a specific job"""
    try:
        job_candidates = db.query(CandidateDB).filter(CandidateDB.job_id == job_id).all()
        return {"candidates": job_candidates, "total": len(job_candidates)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch candidates: {str(e)}")

@app.get("/api/candidates")
async def get_all_candidates(
    current_user: UserDB = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get submitted candidates with submitter info. Vendors see only their own submissions, admin sees all."""
    try:
        from sqlalchemy.orm import joinedload
        
        # Build query
        query = db.query(CandidateDB).options(joinedload(CandidateDB.submitted_by))
        
        # If not admin, filter by current user
        if current_user.email.lower() != ADMIN_EMAIL.lower():
            query = query.filter(CandidateDB.submitted_by_user_id == current_user.id)
        
        candidates = query.all()
        
        result = []
        for c in candidates:
            candidate_dict = {
                "id": c.id,
                "name": c.name,
                "email": c.email,
                "phone": c.phone,
                "job_id": c.job_id,
                "resume_path": c.resume_path,
                "submitted_date": c.submitted_date,
                "status": c.status,
                "bill_rate": c.bill_rate,
                "current_location": c.current_location,
                "primary_skills": c.primary_skills,
                "job_title": c.job_title,
                "years_experience": c.years_experience,
                "tentative_start_date": c.tentative_start_date,
                "rto": c.rto,
                "candidate_summary": c.candidate_summary,
                "submitted_by_user_id": c.submitted_by_user_id,
                "submitted_by": {
                    "id": c.submitted_by.id,
                    "full_name": c.submitted_by.full_name,
                    "email": c.submitted_by.email
                } if c.submitted_by else None
            }
            result.append(candidate_dict)
        
        return {"candidates": result, "total": len(result)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch candidates: {str(e)}")

@app.get("/api/admin/dashboard")
async def get_admin_dashboard(
    current_user: UserDB = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get admin dashboard statistics (admin only)"""
    # Check if admin
    if current_user.email.lower() != ADMIN_EMAIL.lower():
        raise HTTPException(status_code=403, detail="Only admin can access dashboard")
    
    try:
        from sqlalchemy.orm import joinedload, func
        from sqlalchemy import desc
        
        # Get total jobs count from cache
        cached_jobs = ceipal_client._get_cached_jobs() or ceipal_client._jobs_cache or []
        total_jobs = len(cached_jobs)
        
        # Get all candidates with submitter info
        candidates = db.query(CandidateDB).options(joinedload(CandidateDB.submitted_by)).all()
        total_submissions = len(candidates)
        
        # Job-wise submission counts
        job_submissions = {}
        for c in candidates:
            job_title = c.job_title or "Unknown Job"
            if job_title not in job_submissions:
                job_submissions[job_title] = 0
            job_submissions[job_title] += 1
        
        # Sort by submission count (descending)
        job_submissions_sorted = sorted(job_submissions.items(), key=lambda x: x[1], reverse=True)
        
        # Vendor-wise submission counts
        vendor_stats = {}
        for c in candidates:
            if c.submitted_by:
                vendor_email = c.submitted_by.email
                vendor_name = c.submitted_by.full_name or vendor_email
                if vendor_email not in vendor_stats:
                    vendor_stats[vendor_email] = {
                        "name": vendor_name,
                        "email": vendor_email,
                        "total_submissions": 0,
                        "jobs": set()
                    }
                vendor_stats[vendor_email]["total_submissions"] += 1
                vendor_stats[vendor_email]["jobs"].add(c.job_title or "Unknown")
        
        # Convert sets to counts and sort
        vendor_list = []
        for email, stats in vendor_stats.items():
            vendor_list.append({
                "name": stats["name"],
                "email": stats["email"],
                "total_submissions": stats["total_submissions"],
                "unique_jobs": len(stats["jobs"])
            })
        
        vendor_list_sorted = sorted(vendor_list, key=lambda x: x["total_submissions"], reverse=True)
        
        # Status breakdown
        status_counts = {}
        for c in candidates:
            status = c.status or "submitted"
            status_counts[status] = status_counts.get(status, 0) + 1
        
        return {
            "total_jobs": total_jobs,
            "total_submissions": total_submissions,
            "job_submissions": [{"job_title": title, "count": count} for title, count in job_submissions_sorted[:20]],
            "vendor_stats": vendor_list_sorted,
            "status_breakdown": status_counts,
            "last_updated": datetime.now().isoformat()
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch dashboard data: {str(e)}")

@app.patch("/api/candidates/{candidate_id}/status")
async def update_candidate_status(
    candidate_id: str,
    status: str,
    current_user: UserDB = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Update candidate status. Only admin can update status."""
    try:
        # Find the candidate
        candidate = db.query(CandidateDB).filter(CandidateDB.id == candidate_id).first()
        if not candidate:
            raise HTTPException(status_code=404, detail="Candidate not found")
        
        # Check permissions - only admin can update status
        is_admin = current_user.email.lower() == ADMIN_EMAIL.lower()
        if not is_admin:
            raise HTTPException(status_code=403, detail="Only admin can update candidate status")
        
        # Validate status
        valid_statuses = ["submitted", "offer", "decline", "start"]
        if status.lower() not in valid_statuses:
            raise HTTPException(status_code=400, detail=f"Invalid status. Must be one of: {', '.join(valid_statuses)}")
        
        candidate.status = status.lower()
        db.commit()
        
        return {"message": "Status updated successfully", "candidate_id": candidate_id, "status": status.lower()}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to update status: {str(e)}")

@app.get("/api/resumes/{candidate_id}")
async def download_resume(candidate_id: str, db: Session = Depends(get_db)):
    """Download resume file for a candidate"""
    try:
        candidate = db.query(CandidateDB).filter(CandidateDB.id == candidate_id).first()
        if not candidate:
            raise HTTPException(status_code=404, detail="Candidate not found")
        
        resume_path = candidate.resume_path
        if not os.path.exists(resume_path):
            raise HTTPException(status_code=404, detail="Resume file not found")
        
        return FileResponse(
            resume_path,
            filename=os.path.basename(resume_path),
            media_type="application/octet-stream"
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to download resume: {str(e)}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host="0.0.0.0", port=8000, reload=True)
