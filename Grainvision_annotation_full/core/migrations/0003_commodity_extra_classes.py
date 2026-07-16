from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0002_mandi_commodities"),
    ]

    operations = [
        migrations.AddField(
            model_name="commodity",
            name="extra_classes",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
