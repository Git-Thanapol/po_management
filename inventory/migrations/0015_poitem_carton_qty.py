from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('inventory', '0014_poheader_yuan_mode'),
    ]

    operations = [
        migrations.AddField(
            model_name='poitem',
            name='carton_qty',
            field=models.IntegerField(blank=True, null=True, verbose_name='จำนวนลัง'),
        ),
    ]
