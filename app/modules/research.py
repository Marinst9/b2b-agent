import os
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

def prompt_v1_basic(lead: dict) -> str:
    """Prompt 1 — Основен"""
    prompt = f"""
    Напиши кратка B2B email порака на македонски јазик.
    Пораката е ОД нас КОН {lead.get('name')} — не од него/неа.
    Ние нудиме систем за автоматско наоѓање B2B клиенти.
    
    Компанија на примачот: {lead.get('company')}
    Индустрија: {lead.get('industry')}
    
    Максимум 4 реченици. Потпиши се како: Со почит, [Твојот тим]
    """
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=300
    )
    return response.choices[0].message.content

def prompt_v2_detailed(lead: dict) -> str:
    """Prompt 2 — Детален со контекст"""
    prompt = f"""
    Ti si ekspert za B2B sales outreach so 10 godini iskustvo.
    
    Napisi visoko personalizirana email poraka NA MAKEDONSKI JAZIK.
    Pораката е ОД нас КОН примачот — ние нудиме систем за автоматско наоѓање клиенти.
    
    Informacii za primacot:
    - Ime: {lead.get('name')}
    - Kompanija: {lead.get('company')}
    - Industrija: {lead.get('industry')}
    - Zemja: {lead.get('country')}
    
    Pravila:
    1. Zapocni so imeto na primacot — "Zdravo [Ime],"
    2. Pokazi deka ja poznavas nivnata industrija
    3. Objasni TOCNO kako mozeme da im pomogneme da najdat novi klienti
    4. Koristi empathy — pokazi razbiranje za nivniot problem
    5. Zavrsi so EDNO konkretno prasanje za sostanok
    6. Ton: profesionalen no prijatelski
    7. Dolzina: tocno 4-5 recenici
    8. Potpiši se: So pochit, [Tvojot tim]
    9. NE zvuci kako spam
    """
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "Ti si vrven ekspert za personaliziran B2B sales outreach."},
            {"role": "user", "content": prompt}
        ],
        max_tokens=400,
        temperature=0.7
    )
    return response.choices[0].message.content

def prompt_v3_fewshot(lead: dict) -> str:
    """Prompt 3 — Few-shot со примери"""
    prompt = f"""
    Напиши B2B email порака САМО на македонски јазик за следниов примач.
    Без латиница — само македонски јазик и кирилица.
    
    Примач:
    Ime: {lead.get('name')}
    Kompanija: {lead.get('company')}
    Industrija: {lead.get('industry')}
    
    Еве пример на ДОБРА порака:
    ---
    Здраво Александар,
    
    Забележав дека CodeBase има одличен тим од девелопери, но наоѓањето нови клиенти
    честопати одзема премногу време од техничката работа.
    
    Развивам систем кој автоматски ги наоѓа потенцијалните клиенти и праќа
    персонализирани пораки — без да трошите време на тоа.
    
    Дали би имале 15 минути оваа недела да видиме дали има смисла за CodeBase?
    
    Со почит,
    [Твојот тим]
    ---
    
    Сега напиши слична порака за горниот примач. Биди конкретен и персонализиран.
    Максимум 5 реченици. Само македонски јазик.
    """
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "Ти си експерт за B2B sales outreach. Пиши само на македонски јазик со кирилица."},
            {"role": "user", "content": prompt}
        ],
        max_tokens=400,
        temperature=0.5
    )
    return response.choices[0].message.content

def run_research(leads: list) -> list:
    """Генерира пораки со сите 3 промпти и враќа резултати"""
    results = []
    
    for lead in leads:
        print(f"\n=== Истражување за: {lead.get('name')} ===")
        
        print("\n--- Prompt V1 (Основен) ---")
        msg1 = prompt_v1_basic(lead)
        print(msg1)
        
        print("\n--- Prompt V2 (Детален) ---")
        msg2 = prompt_v2_detailed(lead)
        print(msg2)
        
        print("\n--- Prompt V3 (Few-shot) ---")
        msg3 = prompt_v3_fewshot(lead)
        print(msg3)
        
        results.append({
            "lead": lead.get('name'),
            "company": lead.get('company'),
            "prompt_v1": msg1,
            "prompt_v2": msg2,
            "prompt_v3": msg3
        })
    
    return results