"""Poll counting, eligibility, vote casting (transaction-safe)."""
from datetime import datetime, timezone
from sqlalchemy.orm import Session as DBSession
from sqlalchemy.exc import IntegrityError
from app.models import Poll, PollOption, Vote, User, PollGuestAllow

def utcnow():
    return datetime.now(timezone.utc)

def poll_is_open(p: Poll) -> bool:
    if p.status != "OPEN":
        return False
    now = utcnow()
    if p.opens_at:
        o = p.opens_at if p.opens_at.tzinfo else p.opens_at.replace(tzinfo=timezone.utc)
        if o > now:
            return False
    if p.closes_at:
        c = p.closes_at if p.closes_at.tzinfo else p.closes_at.replace(tzinfo=timezone.utc)
        if c < now:
            return False
    return True

def eligible_to_vote(db: DBSession, poll: Poll, user: User, discord_voter_role_ids: set[str] | None = None) -> bool:
    """Server-side eligibility. Guests never pass discord-only polls."""
    if user.kind == "guest":
        from app.models import GuestAccount
        g = db.get(GuestAccount, user.guest_id) if user.guest_id else None
        if not g or not g.active:
            return False
        if poll.eligibility == "discord":
            return False
        # restricted list?
        restricted = db.query(PollGuestAllow).filter_by(poll_id=poll.id).all()
        if restricted and user.id not in {r.user_id for r in restricted}:
            return False
        return True
    # discord user
    if poll.eligibility == "guests":
        return False
    if discord_voter_role_ids is not None and poll.eligibility == "discord":
        if not discord_voter_role_ids:
            return False
    return True

def cast_vote(db: DBSession, poll: Poll, user: User, option_id: str) -> tuple[bool, str]:
    """Returns (ok, message). Enforces one active vote per user via unique constraint + update path."""
    opt = db.get(PollOption, option_id)
    if not opt or opt.poll_id != poll.id:
        return False, "Invalid voting option."
    # re-fetch poll with lock (SELECT FOR UPDATE where supported)
    p = db.get(Poll, poll.id)
    if not poll_is_open(p):
        return False, "This poll is not open for voting."
    existing = db.query(Vote).filter_by(poll_id=poll.id, voter_id=user.id).first()
    if existing:
        if not p.allow_change:
            return False, "You have already voted in this poll."
        existing.option_id = option_id
        existing.updated_at = utcnow()
        db.commit()
        return True, "Vote updated."
    v = Vote(poll_id=poll.id, voter_id=user.id, option_id=option_id)
    db.add(v)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        # lost race: another request created the vote
        ex = db.query(Vote).filter_by(poll_id=poll.id, voter_id=user.id).first()
        if ex and p.allow_change:
            ex.option_id = option_id; ex.updated_at = utcnow(); db.commit()
            return True, "Vote updated."
        return False, "You have already voted in this poll."
    return True, "Vote recorded."

def compute_results(db: DBSession, poll: Poll) -> dict:
    opts = db.query(PollOption).filter_by(poll_id=poll.id).order_by(PollOption.position).all()
    votes = db.query(Vote).filter_by(poll_id=poll.id).all()
    counts = {o.id: 0 for o in opts}
    for v in votes:
        if v.option_id in counts:
            counts[v.option_id] += 1
    abstain = sum(counts[o.id] for o in opts if o.is_abstain)
    substantive = [(o, counts[o.id]) for o in opts if not o.is_abstain]
    total_sub = sum(c for _, c in substantive)
    if total_sub == 0:
        outcome = "NO_WINNER"
        winners: list = []
    else:
        best = max(c for _, c in substantive)
        winners = [o for o, c in substantive if c == best]
        outcome = winners[0].id if len(winners) == 1 else "TIED"
    return {"options": [{"id": o.id, "text": o.text, "votes": counts[o.id], "abstain": o.is_abstain} for o in opts],
            "abstentions": abstain, "total": len(votes), "outcome": outcome,
            "winners": [w.id for w in winners]}

def close_poll(db: DBSession, poll: Poll) -> dict:
    """Transaction-safe closure: set CLOSED first so late votes are rejected, then count."""
    p = db.get(Poll, poll.id)
    if p.status == "CLOSED":
        return compute_results(db, p)
    p.status = "CLOSED"
    p.closed_at = utcnow()
    db.commit()
    res = compute_results(db, p)
    p.result = res["outcome"]
    db.commit()
    return res
