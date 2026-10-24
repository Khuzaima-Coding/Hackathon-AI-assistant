import os
from datetime import datetime, timedelta
from typing import Optional
from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
import jwt
from passlib.context import CryptContext
from openai import OpenAI
from dotenv import load_dotenv

import hashlib

def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return hash_password(plain_password) == hashed_password

load_dotenv()

app = FastAPI(title="HCM Lite AI Backend", version="1.0")

JWT_SECRET = os.getenv("JWT_SECRET", "supersecret123")
# AI_API_KEY = os.getenv("AI_API_KEY", "mock-key")
# openai_client = OpenAI(api_key=AI_API_KEY)
openai_client = OpenAI(api_key="AQ.Ab8RN6L1YPAatM5hfLkNou7KKbGQqezaw_0QjmOTsdM4rpg6SQ")


pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
security = HTTPBearer()

# ==========================================
# IN-MEMORY DATABASE (Zero Setup)
# ==========================================
db = {
    "settings": None,  # {"company_name": str, "office_start": "09:00"}
    "users": [],       # List of users
    "employees": [],   # List of employees
    "attendance": [],  # List of attendance records
    "audit_logs": []   # Audit trail
}

def log_audit(actor: str, action: str, details: dict):
    db["audit_logs"].append({
        "timestamp": datetime.utcnow().isoformat(),
        "actor": actor,
        "action": action,
        "details": details
    })

# ==========================================
# PYDANTIC SCHEMAS
# ==========================================
class SetupRequest(BaseModel):
    company_name: str
    admin_email: str
    admin_password: str

class LoginRequest(BaseModel):
    email: str
    password: str

class EmployeeCreate(BaseModel):
    name: str
    email: str
    department: str
    designation: str
    salary: float

class AIChatRequest(BaseModel):
    message: str

class SmartLeaveRequest(BaseModel):
    text: str

# ==========================================
# AUTH DEPENDENCY & RBAC
# ==========================================
def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    token = credentials.credentials
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        return payload
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

def require_role(allowed_roles: list):
    def role_checker(user: dict = Depends(get_current_user)):
        if user["role"] not in allowed_roles:
            raise HTTPException(status_code=403, detail="Forbidden: Insufficient permissions")
        return user
    return role_checker

# ==========================================
# MODULE 1 & 2: SETUP & AUTH
# ==========================================
@app.get("/api/setup/status")
def setup_status():
    return {"isInitialized": db["settings"] is not None and len(db["users"]) > 0}

@app.post("/api/setup/init")
def init_setup(data: SetupRequest):
    if db["settings"] is not None:
        raise HTTPException(status_code=400, detail="Already initialized")
    
    db["settings"] = {"company_name": data.company_name, "office_start": "09:00"}
    hashed_password = hash_password(data.admin_password) # Updated
    
    admin_user = {
        "id": "usr_admin",
        "email": data.admin_email,
        "password": hashed_password,
        "role": "Admin",
        "name": "Super Admin",
        "employee_id": "EMP_000"
    }
    db["users"].append(admin_user)
    log_audit("System", "INITIALIZE_COMPANY", {"company": data.company_name})
    return {"message": "Company setup successful. You can now login."}

@app.post("/api/auth/login")
def login(data: LoginRequest):
    user = next((u for u in db["users"] if u["email"] == data.email), None)
    # Updated verification check
    if not user or not verify_password(data.password, user["password"]):
        raise HTTPException(status_code=400, detail="Invalid credentials")
    
    token_payload = {
        "id": user["id"],
        "email": user["email"],
        "role": user["role"],
        "employee_id": user["employee_id"],
        "name": user["name"]
    }
    token = jwt.encode(token_payload, JWT_SECRET, algorithm="HS256")
    return {"token": token, "role": user["role"], "name": user["name"]}
# ==========================================
# MODULE 3 & 4: EMPLOYEES & ATTENDANCE
# ==========================================
@app.post("/api/employees")
def register_employee(data: EmployeeCreate, user: dict = Depends(require_role(["Admin", "HR"]))):
    emp_id = f"EMP_{len(db['employees']) + 1:03d}"
    new_emp = data.model_dump() if hasattr(data, 'model_dump') else dict(data)
    new_emp["id"] = emp_id
    db["employees"].append(new_emp)
    
    temp_password = "User123!"
    db["users"].append({
        "id": f"usr_{emp_id}",
        "email": data.email,
        "password": hash_password(temp_password),  # <-- Yahan pwd_context ki jagah hash_password use kiya hai
        "role": "Employee",
        "employee_id": emp_id,
        "name": data.name
    })
    
    log_audit(user["email"], "CREATE_EMPLOYEE", {"employee_id": emp_id, "name": data.name})
    return {"message": "Employee registered", "employee": new_emp, "temp_password": temp_password}
@app.post("/api/attendance/checkin")
def check_in(user: dict = Depends(get_current_user)):
    today = datetime.now().strftime("%Y-%m-%d")
    now_str = datetime.now().strftime("%H:%M:%S")
    
    existing = next((a for a in db["attendance"] if a["employee_id"] == user["employee_id"] and a["date"] == today), None)
    if existing:
        raise HTTPException(status_code=409, detail="Already checked in today")
    
    # Rule: Late after 09:15 AM[cite: 4]
    is_late = now_str > "09:15:00"
    record = {
        "employee_id": user["employee_id"],
        "date": today,
        "check_in": now_str,
        "status": "late" if is_late else "present"
    }
    db["attendance"].append(record)
    log_audit(user["email"], "CHECK_IN", record)
    return {"message": "Check-in successful", "record": record}

# ==========================================
# MODULE 7 & 8: ANALYTICS & AI ASSISTANT
# ==========================================
@app.get("/api/analytics/dashboard")
def get_dashboard(user: dict = Depends(get_current_user)):
    today = datetime.now().strftime("%Y-%m-%d")
    present_today = sum(1 for a in db["attendance"] if a["date"] == today)
    lates_today = sum(1 for a in db["attendance"] if a["date"] == today and a["status"] == "late")
    total_payroll = sum(e.get("salary", 0) for e in db["employees"])
    
    return {
        "headcount": len(db["employees"]),
        "present_today": present_today,
        "lates_today": lates_today,
        "total_payroll_cost": total_payroll
    }

AI_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_headcount",
            "description": "Get total active employee headcount",
            "parameters": {"type": "object", "properties": {}}
        }
    }
]

@app.post("/api/ai/chat")
def ai_chat(data: AIChatRequest, user: dict = Depends(get_current_user)):
    try:
        # Agar OpenAI credits khatam hain, toh hum yahin se smart mock response return kar denge
        message_lower = data.message.lower()
        
        if "present" in message_lower or "attendance" in message_lower:
            today_present = sum(1 for a in db["attendance"])
            reply = f"Based on the system records, there are {today_present} employee(s) checked in today."
        elif "headcount" in message_lower or "employees" in message_lower:
            reply = f"The total active employee headcount in the company is {len(db['employees'])}."
        else:
            reply = f"Hello {user['name']}! I am your HCM Lite AI Assistant. You asked: '{data.message}'. All systems are running smoothly."

        return {"reply": reply, "source": "mock-ai-fallback"}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/ai/smart-leave")
def smart_leave(data: SmartLeaveRequest, user: dict = Depends(get_current_user)):
    try:
        prompt = f"Extract leave details into strict JSON keys: leave_type, start_date, end_date, reason. Text: '{data.text}'"
        resp = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages = [{"role": "user", "content": prompt}],
            response_format={"type": "json_object"}
        )
        return eval(resp.choices[0].message.content)
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to parse leave form.")
@app.post("/api/attendance/checkin")
def check_in(user: dict = Depends(get_current_user)):
    today_date = "2026-10-25"  # Current date context
    # Check if already checked in
    existing = next((a for a in db["attendance"] if a["employee_id"] == user["employee_id"] and a["date"] == today_date), None)
    if existing:
        return {"message": "Already checked in today", "attendance": existing}
    
    attendance_record = {
        "id": f"att_{len(db['attendance']) + 1}",
        "employee_id": user["employee_id"],
        "name": user["name"],
        "date": today_date,
        "status": "Present"
    }
    db["attendance"].append(attendance_record)
    log_audit(user["email"], "CHECK_IN", {"employee_id": user["employee_id"]})
    return {"message": "Check-in successful", "attendance": attendance_record}
@app.get("/api/analytics/dashboard")
def get_analytics(user: dict = Depends(require_role(["Admin", "HR"]))):
    total_employees = len(db["employees"])
    today_attendance = len(db["attendance"])
    
    return {
        "company_name": db["settings"]["company_name"] if db["settings"] else "HCM Lite",
        "total_headcount": total_employees,
        "today_present": today_attendance,
        "today_absent": max(0, total_employees - today_attendance),
        "departments_count": len(set(e.get("department") for e in db["employees"]))
    }

