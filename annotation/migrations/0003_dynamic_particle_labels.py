"""
Particle labels become commodity-specific: drop the fixed `choices` on
`label` / `qc_label_override` and widen them to 32 chars so admin-defined
class values (e.g. "weevil_damaged") are storable. Existing rows keep their
values unchanged — the five default classes remain valid on every commodity.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("annotation", "0002_submission_measurements_done_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="particle",
            name="label",
            field=models.CharField(default="unlabeled", max_length=32),
        ),
        migrations.AlterField(
            model_name="particle",
            name="qc_label_override",
            field=models.CharField(blank=True, max_length=32, null=True),
        ),
    ]
