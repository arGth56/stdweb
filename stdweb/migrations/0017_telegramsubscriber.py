from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('stdweb', '0016_telegram_notice'),
    ]

    operations = [
        migrations.CreateModel(
            name='TelegramSubscriber',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('email', models.EmailField(max_length=254, unique=True)),
                ('token', models.CharField(editable=False, max_length=64, unique=True)),
                ('confirmed', models.BooleanField(default=True)),
                ('created', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'ordering': ['-created'],
            },
        ),
    ]
