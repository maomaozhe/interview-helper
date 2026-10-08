"""User-managed, typed preferences. Temporary model plans never write here."""
from datetime import datetime, timezone
from typing import Any
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from interview_intelligence.contracts import StrictModel
from interview_intelligence.domain.models import UserPreference, now_utc

CHOICES = {
    "design_domain": {"AGENT","BUSINESS_SYSTEM","PRODUCTION_TROUBLESHOOTING","ALL"},
    "coding_focus": {"ENGINEERING","ALGORITHM","MIXED"},
    "language": {"JAVA","PYTHON","CPP","GO","JAVASCRIPT","TYPESCRIPT"},
    "job_family": {"BACKEND","AI_APPLICATION","ALGORITHM","OTHER"},
    "pipeline": {"BM25","HYBRID","DENSE","HYBRID_RERANK"},
}

class PreferenceUpdate(StrictModel):
    value: Any
    source_message: str = Field(min_length=1,max_length=2000)
    expected_version: int = Field(ge=0)
    expires_at: datetime | None = None

def aware(value):
    return value.replace(tzinfo=timezone.utc) if value and value.tzinfo is None else value

class PreferenceService:
    def __init__(self, database, user_id):
        self.database,self.user_id=database,user_id
    def _key(self,key):
        if key not in CHOICES and key != "page_size": raise ValueError("UNKNOWN_PREFERENCE")
    def list(self):
        with self.database.session() as s:
            return [self._view(p) for p in s.scalars(select(UserPreference).where(
                UserPreference.user_id==self.user_id)) if not p.deleted]
    def _view(self,p):
        return {"key":p.key,"value":p.value,"version":p.version,"source_message":p.source_message,
                "expires_at":p.expires_at.isoformat() if p.expires_at else None,
                "active":not p.deleted and (not p.expires_at or aware(p.expires_at)>now_utc())}
    def get(self,key):
        self._key(key)
        with self.database.session() as s:
            p=s.get(UserPreference,(self.user_id,key))
            return self._view(p) if p and not p.deleted else {"key":key,"value":None,"version":p.version if p else 0,"active":False}
    def defaults(self):
        return {p["key"]:p["value"] for p in self.list()
                if p["active"] and (p["key"] in CHOICES or p["key"] == "page_size")}
    def update(self,key,request):
        for attempt in range(2):
            try: return self._update(key,request)
            except IntegrityError:
                if attempt: raise
    def _update(self,key,request):
        self._key(key)
        v=request.value
        if (key=="page_size" and (type(v) is not int or not 1<=v<=100)) or (
            key in CHOICES and (not isinstance(v,str) or v not in CHOICES[key])):
            raise ValueError("INVALID_PREFERENCE_VALUE")
        if request.expires_at and (request.expires_at.tzinfo is None or aware(request.expires_at)<=now_utc()):
            raise ValueError("INVALID_PREFERENCE_EXPIRY")
        with self.database.session() as s,s.begin():
            p=s.get(UserPreference,(self.user_id,key),with_for_update=True)
            if request.expected_version!=(p.version if p else 0): raise ValueError("PREFERENCE_VERSION_CONFLICT")
            if not p:
                p=UserPreference(user_id=self.user_id,key=key,version=0,source_message=request.source_message)
                s.add(p)
            p.value,p.source_message,p.expires_at=v,request.source_message,request.expires_at
            p.deleted,p.version=False,p.version+1
            s.flush()
            return self._view(p)
    def delete(self,key,expected_version):
        self._key(key)
        with self.database.session() as s,s.begin():
            p=s.get(UserPreference,(self.user_id,key),with_for_update=True)
            if expected_version!=(p.version if p else 0): raise ValueError("PREFERENCE_VERSION_CONFLICT")
            if p and not p.deleted:
                p.deleted,p.value,p.version=True,None,p.version+1
            return {"key":key,"deleted":True,"version":p.version if p else 0}
