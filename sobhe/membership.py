"""Automatic media enrollment and period-specific category history."""
from .constants import CATEGORIES
from .models import Advertisement, Outlet, OutletTarget, Period, db


def enroll_period(period):
    """Enroll current active, classified media in a newly created period."""
    outlets = Outlet.query.filter(Outlet.active.is_(True), Outlet.category.in_(CATEGORIES)).all()
    db.session.add_all([OutletTarget(period_id=period.id, outlet_id=outlet.id, category=outlet.category)
                        for outlet in outlets])
    return len(outlets)


def sync_outlet(outlet):
    """Call within the media write transaction; never reclassify recorded allocations."""
    existing = {target.period_id: target for target in OutletTarget.query.filter_by(outlet_id=outlet.id)}
    used = {row[0] for row in db.session.query(Advertisement.period_id).filter_by(outlet_id=outlet.id).distinct()}
    preserved = []
    changed = []
    for period in Period.query.all():
        target = existing.get(period.id)
        if target:
            if target.category != outlet.category:
                if period.id in used:
                    preserved.append(period.id)
                else:
                    target.category = outlet.category
        elif outlet.active and outlet.category in CATEGORIES:
            db.session.add(OutletTarget(period_id=period.id, outlet_id=outlet.id, category=outlet.category))
        else:
            continue
        period.revision += 1
        changed.append(period)
    db.session.flush()
    for period in changed:
        db.session.expire(period, ['targets'])
    return {'periods': [period.id for period in changed], 'preserved_categories': preserved}
