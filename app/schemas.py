from pydantic import BaseModel, Field
from typing import Optional


class CampaignCreate(BaseModel):
    name: str


class DraftGenerateRequest(BaseModel):
    lead_ids: Optional[list[int]] = None
    language: str = "en"
    tone: str = "professional"
    length: str = "medium"


class DraftEditRequest(BaseModel):
    subject: str
    body: str


class SendRequest(BaseModel):
    dry_run: bool = True
    lead_ids: Optional[list[int]] = None


class ResearchRequest(BaseModel):
    force_refresh: bool = False
    page_budget: int = Field(default=5, ge=1, le=10)


class CriteriaRequest(BaseModel):
    product_service: str = ""
    target_industries: list[str] = Field(default_factory=list)
    target_countries: list[str] = Field(default_factory=list)
    preferred_company_size: str = ""
    business_needs: list[str] = Field(default_factory=list)
    exclusion_criteria: list[str] = Field(default_factory=list)
    weights: dict[str, float] = Field(default_factory=dict)


class LabelRequest(BaseModel):
    label: str
    notes: Optional[str] = None


class EvalRatingRequest(BaseModel):
    relevance: Optional[int] = Field(default=None, ge=1, le=5)
    personalization: Optional[int] = Field(default=None, ge=1, le=5)
    notes: Optional[str] = None


class JobCreateRequest(BaseModel):
    lead_ids: Optional[list[int]] = None  # None -> all leads in the campaign
    language: str = "en"
    tone: str = "professional"
    length: str = "medium"
    force_refresh: bool = False
    page_budget: int = Field(default=5, ge=1, le=10)
