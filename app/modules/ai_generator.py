import os
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

PROMPT_VERSION = "basic_v1"
MODEL = "gpt-4o-mini"


def _call_openai(prompt: str) -> str:
    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": "Ti si ekspert za B2B sales outreach."},
            {"role": "user", "content": prompt},
        ],
        max_tokens=300,
    )
    return response.choices[0].message.content


def generate_message(lead: dict, call_model=None) -> str:
    """`call_model`, when given, replaces the actual OpenAI call -- same
    mockable-seam pattern already used by research_extractor.extract_facts
    and draft_generator.generate_draft, added here so the evaluation harness
    (app/evaluation/) can compare this basic prompt against the
    evidence-based one without requiring a real API call in offline mode.
    Defaults to the real call, so existing behavior is unchanged."""
    call_model = call_model or _call_openai
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

    message = call_model(prompt)
    try:
        print(f"Генерирана порака за {lead.get('name')}:")
        print(message)
    except UnicodeEncodeError:
        # Some console encodings (notably Windows' legacy codepages) can't
        # represent Cyrillic -- this is a debug convenience print, not part
        # of the function's actual result, so a console encoding limitation
        # must never fail generation itself.
        pass
    return message


def generate_draft(lead: dict, call_model=None) -> dict:
    """Generates a draft (subject + body) for a lead without sending anything."""
    body = generate_message(lead, call_model=call_model)
    subject = f"Соработка со {lead.get('company', '')}".strip()
    return {"subject": subject, "body": body}