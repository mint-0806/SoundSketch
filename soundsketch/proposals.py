"""Bounded in-memory parameter previews; applying never invokes the planner again."""
from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
import time

from .session import identifier, plan_record, valid_id

PLAN_TTL_SECONDS = 30 * 60
MAX_PLANS = 100


class ProposalStore:
    def __init__(self, sessions):
        self.sessions = sessions
        self.pending = OrderedDict()

    def _prune(self):
        cutoff = time.monotonic() - PLAN_TTL_SECONDS
        for key in list(self.pending):
            if self.pending[key]["created"] <= cutoff:
                del self.pending[key]

    def _offer(self, record, prepared, session=None):
        self._prune()
        record["id"] = None
        if record.get("clarification"):
            return record
        token = identifier()
        record["id"] = token
        self.pending[token] = {"created": time.monotonic(), "record": deepcopy(record),
                               "prepared": deepcopy(prepared),
                               "session_id": session["id"] if session else None,
                               "updated_at": session["updated_at"] if session else None}
        while len(self.pending) > MAX_PLANS:
            self.pending.popitem(last=False)
        return record

    def initial(self, **inputs):
        prepared = self.sessions.prepare_initial(**inputs)
        values, plan, params = prepared
        record = plan_record("initial", plan, params, feedback=values["description"])
        return self._offer(record, prepared)

    def revision(self, session, **inputs):
        prepared = self.sessions.prepare_revision(session, **inputs)
        values, plan, before, after, locks = prepared
        record = plan_record("revision", plan, after, before, locks, values["feedback"])
        return self._offer(record, prepared, session)

    def apply(self, token):
        valid_id(token)
        self._prune()
        proposal = self.pending.get(token)
        if proposal is None:
            raise ValueError("方案已应用、过期或服务已重启，请重新解析。")
        prepared = deepcopy(proposal["prepared"])
        if proposal["session_id"]:
            session = self.sessions.load(proposal["session_id"])
            if session["updated_at"] != proposal["updated_at"]:
                del self.pending[token]
                raise ValueError("会话已变化，旧方案不再适用；请基于当前候选重新解析。")
            result = self.sessions.revise(session, _prepared=prepared)
        else:
            session = self.sessions.create(**prepared[0], _prepared=prepared)
            result = {"session": session, "clarification": None, "status": prepared[1]["mode"]}
        # A retry after a successful application must not create another version.
        del self.pending[token]
        return result
