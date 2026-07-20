from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("annotation", "0003_dynamic_particle_labels"),
    ]

    operations = [
        migrations.AddField(
            model_name="submission",
            name="class_weights",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
