from django.db import migrations


def superusers_to_admin(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    # Any superuser still sitting on the default 'assayer' role becomes 'admin'.
    User.objects.filter(is_superuser=True, role="assayer").update(role="admin")


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0002_initial"),
    ]

    operations = [
        migrations.RunPython(superusers_to_admin, noop),
    ]
