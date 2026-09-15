import os
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

PROMPT_VERSION = "basic_v1"
MODEL = "gpt-4o-mini"

_client: OpenAI | None = None


class MissingProviderCredentialsError(Exception):
    """Raised when a real OpenAI call is requested but OPENAI_API_KEY is not
    configured. Only ever raised at actual call time (see get_client()) --
    importing this module, or any module that imports from it, must never
    require a real API key, since production code always has an injectable
    `call_model` seam (tests/demo/evaluation mock mode use it) that never
    reaches get_client() at all."""


def get_client() -> OpenAI:
    """Lazily constructs (and caches) the real OpenAI client on first use.

    Every real-provider call in this codebase (here and in
    research_extractor.py/draft_generator.py/research.py) goes through this
    function rather than a module-level `client = OpenAI(...)` -- that keeps
    importing api.py/these modules working with no OPENAI_API_KEY set at all
    (required for CI, and for demo mode, where fake_providers.py replaces the
    generate_* functions before they'd ever call this), while a real call
    made without credentials still fails immediately with a clear error
    instead of a cryptic one from deep inside the openai SDK.
    """
    global _client
    if _client is None:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise MissingProviderCredentialsError(
                "OPENAI_API_KEY is not set -- cannot make a real OpenAI call. "
                "Set OPENAI_API_KEY in .env (or the environment), or pass call_model=... "
                "to use a mocked/fake provider instead."
            )
        _client = OpenAI(api_key=api_key)
    return _client


def _call_openai(prompt: str) -> str:
    response = get_client().chat.completions.create(
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