from fastapi import FastAPI, HTTPException, UploadFile, File, Depends, status, Form
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

# Load environment variables
load_dotenv()

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

# Optional: Delete old database on startup (for Render migration)
if os.getenv("DELETE_DB_ON_START") == "true":
    # Handle both relative and absolute paths (sqlite:/// vs sqlite:////)
    db_path = DATABASE_URL.replace("sqlite://", "").lstrip("/")
    if not db_path.startswith("/"):  # relative path
        db_path = "/" + db_path
    if os.path.exists(db_path):
        os.remove(db_path)
        print(f"[MIGRATION] Deleted old database: {db_path}")

# Create tables
Base.metadata.create_all(bind=engine)

# Dependency to get DB session
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

app = FastAPI(title="VMS Backend API", version="1.0.0")

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
CEIPAL_REPORTS_URL = os.getenv("CEIPAL_REPORTS_URL", "https://bi.ceipal.com/ReportDetails/getReportsData/d2RyRHN0Z0s3R29aNWdyN1h2TnBLUT09")
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
    department: str
    location: str
    employment_type: str
    salary_range: Optional[str] = None
    posted_date: datetime
    status: str
    requirements: Optional[str] = None

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


def reverse_string(s: str) -> str:
    """Reverse a string for job code obfuscation"""
    return s[::-1]

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

async def get_current_user(token: str = Depends(HTTPBearer()), db: Session = Depends(get_db)) -> UserDB:
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
    
    user = db.query(UserDB).filter(UserDB.email == token_data.email).first()
    if user is None:
        raise credentials_exception
    if user.is_active != "true":
        raise HTTPException(status_code=400, detail="Inactive user")
    return user

# Auth Endpoints
@app.post("/api/auth/register", response_model=Token)
async def register(user_data: UserCreate, db: Session = Depends(get_db)):
    """Register a new user"""
    # Check if user already exists
    existing_user = db.query(UserDB).filter(UserDB.email == user_data.email).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="Email already registered")
    
    # Create new user
    user_id = str(uuid4())
    hashed_password = get_password_hash(user_data.password)
    
    db_user = UserDB(
        id=user_id,
        email=user_data.email,
        full_name=user_data.full_name,
        hashed_password=hashed_password,
        is_active="true"
    )
    
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    
    # Create access token
    access_token = create_access_token(data={"sub": db_user.email})
    
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": {
            "id": db_user.id,
            "email": db_user.email,
            "full_name": db_user.full_name,
            "is_active": db_user.is_active,
            "created_at": db_user.created_at
        }
    }

@app.post("/api/auth/login", response_model=Token)
async def login(user_data: UserLogin, db: Session = Depends(get_db)):
    """Login user and return JWT token"""
    user = db.query(UserDB).filter(UserDB.email == user_data.email).first()
    if not user:
        raise HTTPException(status_code=401, detail="Incorrect email or password")
    
    if not verify_password(user_data.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Incorrect email or password")
    
    if user.is_active != "true":
        raise HTTPException(status_code=400, detail="Inactive user")
    
    # Create access token
    access_token = create_access_token(data={"sub": user.email})
    
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": {
            "id": user.id,
            "email": user.email,
            "full_name": user.full_name,
            "is_active": user.is_active,
            "created_at": user.created_at
        }
    }

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
        os.makedirs(self.cache_dir, exist_ok=True)

    def _get_cached_jobs(self) -> Optional[List[Job]]:
        """Return cached jobs if less than 1 minute old"""
        if self._jobs_cache and self._jobs_cache_time:
            age = datetime.now() - self._jobs_cache_time
            if age < timedelta(minutes=1):
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
        # Check cache first
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
                
                while has_next and page <= 100:  # Limit to 100 pages (~2000 jobs) for performance
                    # Fetch current page
                    url = f"{self.reports_url}?response_type=1&page={page}"
                    print(f"[Ceipal] Fetching page {page}...")
                    response = await client.get(url, headers=headers, timeout=60.0)
                    response.raise_for_status()
                    
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
            
            # Get the actual job description from Ceipal, fallback to building from other fields
            full_description = job_data.get("JobDescription", "").strip()
            if full_description:
                # Clean up HTML entities that might be in the description
                description = full_description
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
            
            # Get job code and reverse it for vendor display
            actual_job_code = str(job_data.get("JobCode", f"job_{len(jobs)+1}"))
            reversed_job_code = reverse_string(actual_job_code)
            
            # Map Ceipal fields to our Job model
            job = Job(
                id=reversed_job_code,  # Show reversed job code to vendors
                title=job_data.get("JobTitle", "Position Not Specified"),
                description=description,
                department=f"Job Code: {reversed_job_code}",  # Show reversed job code
                location=location if location else "Not specified",
                employment_type=job_data.get("Duration", "Contract"),  # Duration as employment type
                salary_range=salary_range_display,  # Show updated rate to vendors
                posted_date=self._parse_date(job_data.get("JobCreated", job_data.get("CreatedDate"))),
                status=job_data.get("JobStatus", "Open"),
                requirements=f"Bill Rate: {salary_range_display} | Manager: {job_data.get('RecruitmentManager', 'N/A')}"
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
async def get_jobs():
    """Get all active jobs from Ceipal API"""
    try:
        jobs = await ceipal_client.fetch_jobs()
        return JobListResponse(jobs=jobs, total=len(jobs))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch jobs: {str(e)}")

@app.get("/api/jobs/{job_id}", response_model=Job)
async def get_job(job_id: str):
    """Get specific job details"""
    try:
        jobs = await ceipal_client.fetch_jobs()
        job = next((job for job in jobs if job.id == job_id), None)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        return job
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch job: {str(e)}")

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
