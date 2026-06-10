import csv

def load_leads(filepath: str, filters: dict) -> list:
    leads = []
    with open(filepath, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            match = True
            for col, val in filters.items():
                if col in row and val.lower() not in row[col].lower():
                    match = False
                    break
            if match:
                leads.append(dict(row))
    
    print(f"Најдени {len(leads)} лидови после филтрирање")
    return leads