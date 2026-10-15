# Runs once, when a database still on an older version is upgraded to
# 20.0.1.1.0, BEFORE the module's new fields are created.
#
# 1.1.0 adds a Draft/Confirmed state to purchases, and only confirmed ones
# count as obtained. Every purchase recorded before this version was a real
# buy, so it is created as "confirmed" here. Left to the ORM, the new column
# would be filled with the default "draft" and the items would lose their
# obtained quantities.


def migrate(cr, version):
    if not version:  # fresh install, nothing to convert
        return
    cr.execute(
        """
        ALTER TABLE procurement_purchase ADD COLUMN IF NOT EXISTS state varchar;
        UPDATE procurement_purchase SET state = 'confirmed' WHERE state IS NULL;
        """
    )
