"""Evidence-grounded draft generation via the LLM provider.

Mirrors research_extractor.py's pattern: a small Pydantic schema, bounded
retries, and a mockable `call_model` so the provider stays swappable/testable
while production code keeps using the same OpenAI client already configured
in ai_generator.py.

The model is given ONLY the lead's already-validated facts (each with its own
id) and is instructed to cite a fact_id for every specific claim it makes
about the recipient's company, and to never invent funding, growth, staffing,
business-problem, or prior-contact language. Whether those instructions were
actually followed is NOT trusted here -- that verification happens
separately and skeptically in modules/draft_safety.py.
"""
import json
from typing import Optional

from pydantic import BaseModel, Field, ValidationError

from modules.ai_generator import client

MODEL = "gpt-4o-mini"
PROMPT_VERSION = "evidence_v1"
MAX_ATTEMPTS = 3  # 1 initial call + 2 retries

LENGTH_GUIDANCE = {
    "short": "2-3 short sentences.",
    "medium": "4-6 sentences.",
    "long": "7-10 sentences, at most two short paragraphs.",
}

LANGUAGE_NAMES = {"en": "English", "mk": "Macedonian"}

SYSTEM_PROMPT = """You write personalized B2B outreach email drafts for a sales tool.

Rules -- follow them exactly:
1. You may only make specific claims about the recipient's company that are
   directly supported by one of the "verified facts" you were given below.
   For every such claim, add an entry to `claims` with the exact `fact_id`
   it is based on, and `claim` set to that claim as it appears in the body.
2. NEVER invent or imply: funding/investment amounts or rounds, revenue or
   growth figures, headcount or hiring/staffing changes, internal business
   problems or challenges, or any prior contact/conversation with the
   recipient (do not write things like "as we discussed" or "following up on
   our call") -- unless a verified fact explicitly states it.
3. If few or no verified facts are available, write a brief, honest, GENERIC
   outreach message about what we offer, with no fabricated personalization,
   and add no entries to `claims`.
4. Keep proposed benefits (what we could do for them) clearly distinct from
   claims about them (what is already true about their company) -- never
   phrase a benefit we're proposing as if it were an established fact.
5. Return strictly valid JSON matching this schema:
   {"subject": "...", "body": "...", "claims": [{"claim": "...", "fact_id": 123}]}
"""


class DraftClaim(BaseModel):
    claim: str = Field(min_length=1)
    fact_id: int


class GeneratedDraft(BaseModel):
    subject: str = Field(min_length=1)
    body: str = Field(min_length=1)
    claims: list[DraftClaim] = Field(default_factory=list)


class DraftGenerationError(Exception):
    """Raised when the model never returned a schema-valid response within MAX_ATTEMPTS."""


class GeneratedDraftResult:
    def __init__(self, subject: str, body: str, claims: list, model: str, usage: Optional[dict]):
        self.subject = subject
        self.body = body
        self.claims = claims
        self.model = model
        self.usage = usage or {}


def _build_prompt(company, contact_name, product_service, facts, language, tone, length) -> str:
    facts_block = "\n".join(f"- id={f['id']} [{f['category']}] {f['text']}" for f in facts) or "(no verified facts available)"
    return f"""Company: {company}
Contact name: {contact_name}
What we offer: {product_service or "(not specified)"}

Verified facts you may cite (ONLY these -- cite by id, never invent new ones):
{facts_block}

Write in {LANGUAGE_NAMES.get(language, language)}. Tone: {tone}. Length: {LENGTH_GUIDANCE.get(length, length)}
"""


def _call_openai(prompt: str):
    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        response_format={"type": "json_object"},
        max_tokens=800,
        temperature=0.4,
    )
    usage = {}
    if getattr(response, "usage", None):
        usage = {
            "prompt_tokens": response.usage.prompt_tokens,
            "completion_tokens": response.usage.completion_tokens,
            "total_tokens": response.usage.total_tokens,
        }
    return response.choices[0].message.content, usage


def generate_draft(
    *,
    company: str,
    contact_name: str,
    product_service: str,
    facts: list,
    language: str = "en",
    tone: str = "professional",
    length: str = "medium",
    call_model=None,
) -> GeneratedDraftResult:
    call_model = call_model or _call_openai
    prompt = _build_prompt(company, contact_name, product_service, facts, language, tone, length)

    last_error = None
    for _ in range(MAX_ATTEMPTS):
        try:
            raw, usage = call_model(prompt)
            data = json.loads(raw)
            parsed = GeneratedDraft.model_validate(data)
            return GeneratedDraftResult(parsed.subject, parsed.body, parsed.claims, MODEL, usage)
        except (json.JSONDecodeError, ValidationError, TypeError) as e:
            last_error = e
            continue
        except Exception as e:
            last_error = e
            continue

    raise DraftGenerationError(f"draft generation failed after {MAX_ATTEMPTS} attempt(s): {last_error}")
