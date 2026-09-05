from django.db import models
from django.db.models.signals import pre_delete, post_save
from django.dispatch import receiver
from django.utils.timezone import now
from django.contrib.auth.models import User
from django.conf import settings

import os, shutil
import datetime


class Task(models.Model):
    # path = models.CharField(max_length=250, blank=False, unique=True, editable=False) # Base dir where task processing will be performed
    original_name = models.CharField(max_length=250, blank=False) # Original filename
    title = models.CharField(max_length=250, blank=True) # Optional title or comment

    state = models.CharField(max_length=50, blank=False, default='initial') # State of the task

    celery_id = models.CharField(max_length=50, blank=True, null=True, default=None, editable=False) # Celery task ID, when running

    user =  models.ForeignKey(User, on_delete=models.CASCADE)

    created = models.DateTimeField(auto_now_add=True)
    modified = models.DateTimeField(auto_now=True) # Updated on every .save()
    completed = models.DateTimeField(default=now, editable=False) # Manually updated on finishing the processing

    config = models.JSONField(default=dict, blank=True) #

    def path(self):
        return os.path.join(settings.TASKS_PATH, str(self.id))

    def complete(self):
        self.completed = now()

    def __str__(self):
        return f"{self.id}: {self.user.username} : {self.original_name}"

    class Meta:
        permissions = [
            ('skyportal_upload', 'Can upload the task results to SkyPortal')
        ]


@receiver(pre_delete, sender=Task)
def delete_task_hook(sender, instance, using, **kwargs):
    path = instance.path()

    # Cleanup the data on filesystem related to this model
    if os.path.exists(path):
        shutil.rmtree(path)


class Preset(models.Model):
    name = models.CharField(max_length=250, blank=False) # Preset name
    config = models.JSONField(default=dict, blank=True, help_text='Initial config for the task, in JSON format')
    files = models.TextField(blank=True, help_text='Files to be copied into new task, one per line') # Files to be copied into new task, one per line

    def __str__(self):
        return f"{self.id}: {self.name}"


class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    share_public_lightcurves = models.BooleanField(
        default=False,
        help_text='Share my measures in the public lightcurves',
    )
    affiliation = models.CharField(
        max_length=120,
        blank=True,
        help_text='Observatory or institute, shown with your name on public pages',
    )
    telegram_radius_arcmin = models.FloatField(
        default=1.0,
        help_text='Cone radius (arcmin) used to associate a TNS/EP alert with a telegram',
    )
    telegram_alert_age_hours = models.FloatField(
        default=24.0,
        help_text='Expected alert age for a telegram follow-up (hours). Older alerts are flagged in the draft.',
    )

    def __str__(self):
        flag = 'shared' if self.share_public_lightcurves else 'private'
        return f"{self.user.username}: {flag}"


def get_user_profile(user):
    profile, _ = UserProfile.objects.get_or_create(user=user)
    return profile


@receiver(post_save, sender=User)
def ensure_user_profile(sender, instance, created, **kwargs):
    if created:
        UserProfile.objects.get_or_create(user=instance)


class LightcurvePoint(models.Model):
    """Forced-photometry point. Public pages show only published points.

    Every finished photometry is stored. The observer publishes a target from
    the task page; that does not publish their other targets.
    """
    task = models.OneToOneField(Task, on_delete=models.CASCADE, related_name='lightcurve_point')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='lightcurve_points')

    ra = models.FloatField()
    dec = models.FloatField()
    filt = models.CharField(max_length=50)
    mag_filter_name = models.CharField(max_length=50, blank=True)
    target_name = models.CharField(max_length=250, blank=True)

    mjd = models.FloatField()
    time_iso = models.CharField(max_length=50, blank=True)

    mag = models.FloatField(null=True, blank=True)
    magerr = models.FloatField(null=True, blank=True)
    mag_limit = models.FloatField(null=True, blank=True)
    is_detection = models.BooleanField(default=False)
    is_diff = models.BooleanField(default=False)
    published = models.BooleanField(default=False)

    created = models.DateTimeField(auto_now_add=True)
    modified = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=['ra', 'dec']),
            models.Index(fields=['mjd']),
            models.Index(fields=['filt']),
        ]

    def __str__(self):
        mag = f"{self.mag:.3f}" if self.mag is not None else 'limit'
        return f"{self.task_id} {self.filt} {mag} @{self.mjd:.5f}"


class AlertMeta(models.Model):
    """TNS (or first-epoch) discovery time used as alert age zero-point."""
    name_key = models.CharField(max_length=80, unique=True)
    display_name = models.CharField(max_length=80, blank=True)
    discovery_mjd = models.FloatField(null=True, blank=True)
    discovery_iso = models.CharField(max_length=50, blank=True)
    tns_url = models.CharField(max_length=250, blank=True)
    source = models.CharField(max_length=20, blank=True)
    tns_name = models.CharField(max_length=80, blank=True)
    obj_type = models.CharField(max_length=50, blank=True)
    host_name = models.CharField(max_length=120, blank=True)
    redshift = models.CharField(max_length=20, blank=True)
    disc_mag = models.FloatField(null=True, blank=True)
    disc_filter = models.CharField(max_length=40, blank=True)
    reporting_group = models.CharField(max_length=120, blank=True)
    tns_ra = models.FloatField(null=True, blank=True)
    tns_dec = models.FloatField(null=True, blank=True)
    fetched = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.display_name or self.name_key} {self.discovery_iso}"


class TelegramNotice(models.Model):
    """One telegram: one text, one measure (one task), one alert."""
    point = models.OneToOneField(
        LightcurvePoint, on_delete=models.CASCADE, related_name='telegram',
    )
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='telegram_notices')
    object_name = models.CharField(max_length=120, help_text='Alert name (TNS, EP-WXT, …)')
    alert_url = models.CharField(max_length=250, blank=True)
    alert_kind = models.CharField(max_length=20, blank=True)
    title = models.CharField(max_length=200, blank=True)
    body = models.TextField()
    created = models.DateTimeField(auto_now_add=True)
    modified = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created']

    def __str__(self):
        return f"{self.object_name} ({self.user.username})"
