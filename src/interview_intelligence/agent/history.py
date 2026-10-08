"""Read-only, user-bound keyset pagination of conversations and stored turns."""
from datetime import datetime

from sqlalchemy import and_, exists, or_, select

from interview_intelligence.analytics.scope import sign_scope, verify_scope
from interview_intelligence.domain.models import AgentConversation, AgentTurn


class ConversationHistory:
    def __init__(self, database, user_id, signing_key):
        self.database, self.user_id, self.signing_key = database, user_id, signing_key

    def _cursor(self, token, kind, scope):
        payload = verify_scope(token, key=self.signing_key, corpus_revision=0, max_age=86400 * 30)
        if payload.get("user") != self.user_id or payload.get("kind") != kind or payload.get("scope") != scope:
            raise ValueError("INVALID_CURSOR_SCOPE")
        try:
            return datetime.fromisoformat(payload["time"]), payload["id"]
        except (ValueError, KeyError):
            raise ValueError("INVALID_CURSOR_SCOPE")

    def _next(self, row, kind, scope):
        return sign_scope({"user": self.user_id, "kind": kind, "scope": scope,
                           "time": row.created_at.isoformat(), "id": row.id},
                          key=self.signing_key, corpus_revision=0)

    def conversations(self, query="", cursor=None, limit=30):
        with self.database.session() as session:
            statement = select(AgentConversation).where(AgentConversation.user_id == self.user_id)
            statement = statement.where(exists(select(AgentTurn.id).where(
                AgentTurn.conversation_id == AgentConversation.id, AgentTurn.user_id == self.user_id)))
            if query:
                statement = statement.where(exists(select(AgentTurn.id).where(
                    AgentTurn.conversation_id == AgentConversation.id, AgentTurn.user_id == self.user_id,
                    AgentTurn.message.contains(query, autoescape=True))))
            if cursor:
                timestamp, identity = self._cursor(cursor, "conversations", query)
                statement = statement.where(or_(AgentConversation.created_at < timestamp,
                    and_(AgentConversation.created_at == timestamp, AgentConversation.id < identity)))
            rows = list(session.scalars(statement.order_by(AgentConversation.created_at.desc(),
                                                           AgentConversation.id.desc()).limit(limit + 1)))
            items = []
            for c in rows[:limit]:
                title = session.scalar(select(AgentTurn.message).where(AgentTurn.conversation_id == c.id)
                    .order_by(AgentTurn.created_at, AgentTurn.id).limit(1))
                items.append({"conversation_id": c.id, "title": title[:100], "version": c.version,
                              "created_at": c.created_at.isoformat(), "updated_at": c.updated_at.isoformat()})
            return {"items": items, "next_cursor": self._next(rows[limit - 1], "conversations", query)
                    if len(rows) > limit else None}

    def turns(self, conversation_id, cursor=None, limit=20):
        with self.database.session() as session:
            c = session.get(AgentConversation, conversation_id)
            if not c or c.user_id != self.user_id:
                raise KeyError("CONVERSATION_NOT_FOUND")
            statement = select(AgentTurn).where(AgentTurn.conversation_id == c.id,
                                               AgentTurn.user_id == self.user_id)
            if cursor:
                timestamp, identity = self._cursor(cursor, "turns", c.id)
                statement = statement.where(or_(AgentTurn.created_at < timestamp,
                    and_(AgentTurn.created_at == timestamp, AgentTurn.id < identity)))
            rows = list(session.scalars(statement.order_by(AgentTurn.created_at.desc(),
                                                          AgentTurn.id.desc()).limit(limit + 1)))
            turns = [{"run_id": t.id, "request_id": t.request_id, "message": t.message,
                      "status": t.status, "result": t.response, "error_code": t.error_code,
                      "created_at": t.created_at.isoformat()} for t in rows[:limit]]
            return {"conversation_id": c.id, "version": c.version, "state": c.state, "turns": turns[::-1],
                    "next_cursor": self._next(rows[limit - 1], "turns", c.id) if len(rows) > limit else None}
