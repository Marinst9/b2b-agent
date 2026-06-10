import time
from modules.csv_handler import load_leads
from modules.enrichment import get_email
from modules.ai_generator import generate_message
from modules.email_sender import send_email

def run_agent(csv_path: str, filters: dict):
    print("=== B2B Аутрич Агент ===\n")

    leads = load_leads(csv_path, filters)

    for lead in leads:
        print(f"\n--- Обработка: {lead['name']} ---")

        name_parts = lead['name'].split()
        first_name = name_parts[0] if len(name_parts) > 0 else ""
        last_name = name_parts[1] if len(name_parts) > 1 else ""

        email_data = get_email(first_name, last_name, "techsoft.mk")
        email = email_data['email'] or "test@example.com"

        message = generate_message(lead)
        send_email(email, f"Соработка со {lead['company']}", message)
        
        time.sleep(2)

    print(f"\n=== Завршено! ===")

if __name__ == "__main__":
    run_agent(
        csv_path="../data/test_leads.csv",
        filters={"industry": "IT"}
    )