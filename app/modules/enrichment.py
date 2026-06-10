import requests
import os
from dotenv import load_dotenv

load_dotenv()

HUNTER_API_KEY = os.getenv("HUNTER_API_KEY")

def get_email(first_name: str, last_name: str, company_domain: str) -> dict:
    """
    Наоѓа email адреса преку Hunter.io API
    """
    url = "https://api.hunter.io/v2/email-finder"
    
    params = {
        "domain": company_domain,
        "first_name": first_name,
        "last_name": last_name,
        "api_key": HUNTER_API_KEY
    }
    
    response = requests.get(url, params=params)
    data = response.json()
    
    if data.get("data") and data["data"].get("email"):
        email = data["data"]["email"]
        score = data["data"].get("score", 0)
        print(f"Најден email: {email} (score: {score})")
        return {"email": email, "score": score}
    else:
        print(f"Не е најден email за {first_name} {last_name}")
        return {"email": None, "score": 0}