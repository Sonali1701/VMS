from fastapi import FastAPI, HTTPException, UploadFile, File, Depends, status, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.security import HTTPBearer
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
from sqlalchemy import create_engine, Column, String, DateTime, Integer
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session

# Load environment variables
load_dotenv()

# Database setup
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./data/vms.db")
# Ensure directory exists for SQLite
if DATABASE_URL.startswith("sqlite:///./"):
    db_path = DATABASE_URL.replace("sqlite:///./", "")
    db_dir = os.path.dirname(db_path)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Database Models
class CandidateDB(Base):
    __tablename__ = "candidates"
    
    id = Column(String, primary_key=True, index=True)
    name = Column(String, nullable=False)
    email = Column(String, nullable=False)
    phone = Column(String, nullable=True)
    job_id = Column(String, nullable=False)
    resume_path = Column(String, nullable=False)
    submitted_date = Column(DateTime, default=datetime.utcnow)
    status = Column(String, default="submitted")

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
CEIPAL_CACHE_DIR = os.getenv("CEIPAL_CACHE_DIR", "./cache")
DEBUG = os.getenv("DEBUG", "False").lower() in {"1", "true", "yes", "y"}

UPLOAD_DIR = os.getenv("UPLOAD_DIR", "./uploads")
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

# Serve uploaded resumes statically
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
        os.makedirs(self.cache_dir, exist_ok=True)

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
        """Fetch all jobs from Ceipal Reports API with pagination support"""
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
                
                while has_next and page <= 50:  # Limit to 50 pages (1000 jobs) to prevent infinite loops
                    # Fetch current page
                    url = f"{self.reports_url}?response_type=1&page={page}"
                    response = await client.get(url, headers=headers)
                    response.raise_for_status()
                    
                    reports_data = response.json()
                    
                    # Cache first page for inspection
                    if page == 1:
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
                    
                    # Check if there's a next page
                    has_next = bool(
                        reports_data.get("has_next_page") or 
                        reports_data.get("next_page")
                    )
                    
                    # Also check record count vs what we have
                    total_records = int(reports_data.get("record_count", 0))
                    if len(all_jobs) >= total_records:
                        has_next = False
                    
                    page += 1
                
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
            
            # Map Ceipal fields to our Job model
            job = Job(
                id=str(job_data.get("JobCode", f"job_{len(jobs)+1}")),
                title=job_data.get("JobTitle", "Position Not Specified"),
                description=description,
                department=job_data.get("Client", "Healthcare"),  # Use Client as department/company
                location=location if location else "Not specified",
                employment_type=job_data.get("Duration", "Contract"),  # Duration as employment type
                salary_range=job_data.get("ClientBillRateSalary", job_data.get("BillRate", "Not specified")),
                posted_date=self._parse_date(job_data.get("JobCreated", job_data.get("CreatedDate"))),
                status=job_data.get("JobStatus", "Open"),
                requirements=f"Bill Rate: {job_data.get('BillRate', 'N/A')} | Manager: {job_data.get('RecruitmentManager', 'N/A')}"
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
    phone: Optional[str] = Form(None),
    job_id: str = Form(...),
    resume: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    """Submit candidate resume for a job"""
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
        
        # Store candidate in database
        db_candidate = CandidateDB(
            id=candidate_id,
            name=candidate_name,
            email=email,
            phone=phone,
            job_id=job_id,
            resume_path=file_path,
            submitted_date=datetime.now(),
            status="submitted"
        )
        db.add(db_candidate)
        db.commit()
        db.refresh(db_candidate)
        
        return {
            "message": "Candidate submitted successfully",
            "candidate_id": candidate_id,
            "status": "submitted"
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
async def get_all_candidates(db: Session = Depends(get_db)):
    """Get all submitted candidates"""
    try:
        candidates = db.query(CandidateDB).all()
        return {"candidates": candidates, "total": len(candidates)}
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
