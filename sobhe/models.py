"""SQLite-backed records. Box values are integer hundredths."""
from datetime import datetime, timezone
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import CheckConstraint, UniqueConstraint

db = SQLAlchemy()


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    name = db.Column(db.String(150), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False)
    active = db.Column(db.Boolean, default=True, nullable=False)
    auth_version = db.Column(db.Integer, default=1, nullable=False)
    __table_args__ = (CheckConstraint("role IN ('admin','operator')"),)


class Outlet(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), unique=True, nullable=False)
    active = db.Column(db.Boolean, default=True, nullable=False)
    # NULL is reserved for legacy media awaiting explicit classification.
    category = db.Column(db.String(20))
    __table_args__ = (CheckConstraint("category IN ('local','national')"),)


class Organization(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), unique=True, nullable=False)
    active = db.Column(db.Boolean, default=True, nullable=False)


class Subject(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), unique=True, nullable=False)
    active = db.Column(db.Boolean, default=True, nullable=False)


class Period(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    year = db.Column(db.Integer, nullable=False)
    active = db.Column(db.Boolean, default=False, nullable=False)
    recovery = db.Column(db.Boolean, default=False, nullable=False)
    revision = db.Column(db.Integer, default=1, nullable=False)
    targets = db.relationship('OutletTarget', back_populates='period', order_by='OutletTarget.outlet_id',
                              lazy='selectin')
    __table_args__ = (UniqueConstraint('year'),
                      CheckConstraint('year BETWEEN 1200 AND 1600'))


class OutletTarget(db.Model):
    """Automatically managed period membership; category preserves recorded history."""
    id = db.Column(db.Integer, primary_key=True)
    period_id = db.Column(db.Integer, db.ForeignKey('period.id'), nullable=False, index=True)
    outlet_id = db.Column(db.Integer, db.ForeignKey('outlet.id'), nullable=False)
    category = db.Column(db.String(20), nullable=False)
    # Kept for schema/audit compatibility only. Allocation never reads legacy shares.
    share_bp = db.Column(db.Integer, default=0, nullable=False)
    period = db.relationship('Period', back_populates='targets')
    outlet = db.relationship('Outlet', lazy='joined')
    __table_args__ = (UniqueConstraint('period_id', 'outlet_id'),
                      CheckConstraint("category IN ('local','national')"),
                      CheckConstraint('share_bp BETWEEN 0 AND 10000'))


class Advertisement(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    ma_reference = db.Column(db.BigInteger, unique=True, nullable=False)
    organization_id = db.Column(db.Integer, db.ForeignKey('organization.id'), nullable=False, index=True)
    outlet_id = db.Column(db.Integer, db.ForeignKey('outlet.id'), nullable=False, index=True)
    period_id = db.Column(db.Integer, db.ForeignKey('period.id'), nullable=False, index=True)
    allocation_date = db.Column(db.Date, nullable=False, index=True)
    volume_units = db.Column(db.BigInteger)
    subject_id = db.Column(db.Integer, db.ForeignKey('subject.id'))
    subject_description = db.Column(db.Text, default='', nullable=False)
    letter_number = db.Column(db.String(100), default='', nullable=False)
    letter_date = db.Column(db.Date)
    province = db.Column(db.String(60), default='', nullable=False)
    ad_type = db.Column(db.String(60), default='', nullable=False)
    print_status = db.Column(db.String(60), default='منتظر چاپ', nullable=False)
    proposed_date = db.Column(db.Date)
    final_print_date = db.Column(db.Date)
    approval_date = db.Column(db.Date, index=True)
    ad_text = db.Column(db.Text, default='', nullable=False)
    page_number = db.Column(db.Integer)
    page_type = db.Column(db.String(100), default='', nullable=False)
    distribution_location = db.Column(db.String(500), default='', nullable=False)
    box_size = db.Column(db.String(100), default='', nullable=False)
    headline_units = db.Column(db.BigInteger)
    approved_units = db.Column(db.BigInteger)
    approver_name = db.Column(db.String(150), default='', nullable=False)
    amount = db.Column(db.BigInteger)
    approved_amount = db.Column(db.BigInteger)
    notes = db.Column(db.Text, default='', nullable=False)
    file_key = db.Column(db.String(80))
    file_name = db.Column(db.String(255))
    file_mime = db.Column(db.String(60))
    created_at = db.Column(db.DateTime, default=now, nullable=False, index=True)
    updated_at = db.Column(db.DateTime, default=now, onupdate=now, nullable=False)
    creator_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    voided_at = db.Column(db.DateTime)
    void_reason = db.Column(db.String(1000), default='', nullable=False)
    version = db.Column(db.Integer, default=1, nullable=False)
    organization = db.relationship('Organization')
    outlet = db.relationship('Outlet')
    subject = db.relationship('Subject')
    period = db.relationship('Period')
    creator = db.relationship('User')
    __table_args__ = (CheckConstraint('ma_reference > 0'), CheckConstraint('volume_units > 0'),
                      CheckConstraint('amount IS NULL OR amount >= 0'),
                      CheckConstraint('approved_amount IS NULL OR approved_amount >= 0'),
                      CheckConstraint('headline_units IS NULL OR headline_units >= 0'),
                      CheckConstraint('approved_units IS NULL OR approved_units >= 0'),
                      CheckConstraint('page_number IS NULL OR page_number > 0'))


class AuditEvent(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    actor_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    action = db.Column(db.String(60), nullable=False)
    target = db.Column(db.String(100), nullable=False)
    detail = db.Column(db.Text, default='', nullable=False)
    created_at = db.Column(db.DateTime, default=now, nullable=False)
    actor = db.relationship('User')


class Setting(db.Model):
    key = db.Column(db.String(60), primary_key=True)
    value = db.Column(db.String(200), nullable=False)


class SchemaVersion(db.Model):
    version = db.Column(db.Integer, primary_key=True)
    applied_at = db.Column(db.DateTime, default=now, nullable=False)
