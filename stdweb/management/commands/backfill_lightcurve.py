from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils.timezone import now

from stdweb import models
from stdweb import lightcurve


class Command(BaseCommand):
    help = 'Ingest forced photometry from existing tasks into the lightcurve table'

    def add_arguments(self, parser):
        parser.add_argument('task_ids', nargs='*', type=int)
        parser.add_argument(
            '--days', type=int, default=None,
            help='Only tasks created in the last N days',
        )

    def handle(self, *args, **options):
        ids = options['task_ids']
        qs = models.Task.objects.all().order_by('id')
        if ids:
            qs = qs.filter(id__in=ids)
        if options['days']:
            qs = qs.filter(created__gte=now() - timedelta(days=options['days']))

        n_ok = n_skip = n_err = 0
        for task in qs.iterator():
            try:
                point = lightcurve.upsert_from_task(task)
            except Exception as exc:
                n_err += 1
                self.stderr.write(f'{task.id}: {exc}')
                continue
            if point:
                n_ok += 1
            else:
                n_skip += 1

        self.stdout.write(f'ingested={n_ok} skipped={n_skip} errors={n_err}')
