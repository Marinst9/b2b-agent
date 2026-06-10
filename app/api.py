from fastapi import FastAPI, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from modules.csv_handler import load_leads
from modules.enrichment import get_email
from modules.ai_generator import generate_message
from modules.email_sender import send_email
from database import init_db, SessionLocal, Lead
import time
import csv
import io

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

init_db()

uploaded_leads_cache = []

@app.post("/upload-csv")
async def upload_csv(file: UploadFile = File(...)):
    global uploaded_leads_cache
    contents = await file.read()
    decoded = contents.decode("utf-8")
    reader = csv.DictReader(io.StringIO(decoded))
    leads = [dict(row) for row in reader]
    uploaded_leads_cache = leads
    return {"leads": leads, "count": len(leads)}

@app.get("/leads")
def get_leads():
    db = SessionLocal()
    leads = db.query(Lead).all()
    db.close()
    return [{"id": l.id, "name": l.name, "company": l.company, "industry": l.industry, "email": l.email, "status": l.status, "opened": l.opened} for l in leads]

@app.post("/run")
def run_agent():
    global uploaded_leads_cache
    db = SessionLocal()
    db.query(Lead).delete()
    db.commit()

    if uploaded_leads_cache:
        leads = uploaded_leads_cache
    else:
        leads = load_leads("../data/test_leads.csv", {"industry": "IT"})

    for lead in leads:
        name_parts = lead["name"].split()
        first_name = name_parts[0] if len(name_parts) > 0 else ""
        last_name = name_parts[1] if len(name_parts) > 1 else ""

        domain = lead.get("company", "example").lower().replace(" ", "") + ".mk"
        email_data = get_email(first_name, last_name, domain)
        email = email_data["email"] or lead.get("email") or "test@example.com"

        message = generate_message(lead)
        success = send_email(email, f"Соработка со {lead['company']}", message)

        db_lead = Lead(
            name=lead["name"],
            company=lead["company"],
            industry=lead.get("industry", ""),
            country=lead.get("country", ""),
            email=email,
            message=message,
            status="sent" if success else "waiting",
            opened=False
        )
        db.add(db_lead)
        db.commit()
        time.sleep(2)

    total = db.query(Lead).count()
    db.close()
    return {"message": f"Обработени {total} лидови"}

@app.get("/stats")
def get_stats():
    db = SessionLocal()
    total = db.query(Lead).count()
    sent = db.query(Lead).filter(Lead.status == "sent").count()
    opened = db.query(Lead).filter(Lead.opened == True).count()
    db.close()
    return {"total": total, "sent": sent, "opened": opened, "replied": 0}

@app.post("/research")
def run_research_endpoint():
    global uploaded_leads_cache
    from modules.research import run_research
    if uploaded_leads_cache:
        leads = uploaded_leads_cache
    else:
        leads = load_leads("../data/test_leads.csv", {"industry": "IT"})
    results = run_research(leads)
    return {"results": results}