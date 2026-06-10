import os
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

def generate_message(lead: dict) -> str:
    prompt = f"""
    Напиши кратка и персонализирана B2B email порака на македонски јазик.
    
    Информации за лидот:
    - Име: {lead.get('name')}
    - Компанија: {lead.get('company')}
    - Индустрија: {lead.get('industry')}
    - Земја: {lead.get('country')}
    
    Правила:
    1. Почни со лично обраќање по име
    2. Спомени ја компанијата конкретно
    3. Објасни кратко како можеме да им помогнеме со наоѓање нови клиенти
    4. Заврши со конкретно прашање за состанок
    5. Максимум 5 реченици
    6. Не звучи како spam
    """

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "Ti si ekspert za B2B sales outreach."},
            {"role": "user", "content": prompt}
        ],
        max_tokens=300
    )

    message = response.choices[0].message.content
    print(f"Генерирана порака за {lead.get('name')}:")
    print(message)
    return message