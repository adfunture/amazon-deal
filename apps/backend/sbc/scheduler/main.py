"""Scheduler-Service: plant periodische Jobs in die Dramatiq-Queues ein.

Läuft als eigener, dauerhaft laufender Railway-Service (genau 1 Replica). Die Jobs selbst
führt der Worker aus; der Scheduler enthält keine Fachlogik. Zeitplan in Europe/Berlin.
"""

from __future__ import annotations

import logging
import os

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from sbc.worker import tasks

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
log = logging.getLogger("sbc.scheduler")

SCHEDULE = {
    # Name: (Cron, Actor, Argumente)
    "fx_rates": ("15 16 * * 1-5", tasks.refresh_fx_rates, ()),  # EZB veröffentlicht ~16:00 MEZ
    "reanalyze_stale": ("0 */6 * * *", tasks.reanalyze_stale, (24,)),
    "daily_report": ("30 6 * * *", tasks.daily_report, ()),
    "cleanup_files": ("0 3 * * *", tasks.cleanup_expired_files, ()),
}


def main() -> None:
    sched = BlockingScheduler(timezone="Europe/Berlin")
    for name, (cron, actor, args) in SCHEDULE.items():
        override = os.environ.get(f"SCHEDULE_{name.upper()}")
        if override == "off":
            log.info("Job %s deaktiviert", name)
            continue
        sched.add_job(lambda a=actor, ar=args: a.send(*ar), CronTrigger.from_crontab(override or cron),
                      id=name, max_instances=1, coalesce=True, misfire_grace_time=3600)
        log.info("Job %s geplant: %s", name, override or cron)
    sched.start()


if __name__ == "__main__":
    main()
