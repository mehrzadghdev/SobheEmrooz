"""Allowlisted search and ordering for tables paginated by the database."""
from sqlalchemy import String, Integer, cast, or_, select, func
from .models import Advertisement, Organization, Outlet, Subject


def ad_table_query(query, args):
    fields = {column.name: column for column in Advertisement.__table__.columns}
    fields['ma_reference'] = Advertisement.display_reference
    from .directive import DirectiveRecord
    allocation = DirectiveRecord.__table__.alias('allocation_reference')
    order = DirectiveRecord.__table__.alias('official_order')
    official = select(func.json_extract(order.c.payload, '$.official_reference')).select_from(
        order.join(allocation, order.c.id == cast(func.json_extract(allocation.c.payload, '$.order_id'), Integer))).where(
        allocation.c.kind == 'allocation', allocation.c.reference == cast(Advertisement.id, String),
        order.c.kind == 'order').correlate(Advertisement).scalar_subquery()
    fields['official_reference'] = official
    fields.update(organization=Organization.name, outlet=Outlet.name, subject=Subject.name,
                  file=Advertisement.file_name)
    query = query.outerjoin(Organization).outerjoin(Outlet).outerjoin(Subject)
    return table_query(query, args, fields, Advertisement.id)


def table_query(query, args, fields, identity):
    search = args.get('q', '').strip()[:200]
    if search:
        # Treat %, _ and backslashes as literal text, rather than SQL wildcards.
        search = search.translate(str.maketrans('۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩', '01234567890123456789'))
        pattern = '%' + search.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
        query = query.filter(or_(*(cast(field, String).ilike(pattern, escape='\\')
                                   for field in fields.values())))
    key = args.get('sort', 'id')
    direction = args.get('direction', 'desc')
    if key not in fields or direction not in ('asc', 'desc'):
        raise ValueError('ستون یا جهت مرتب‌سازی معتبر نیست.')
    field = fields[key]
    order = field.asc() if direction == 'asc' else field.desc()
    return query.order_by(None).order_by(order, identity.desc())
