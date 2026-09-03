import logging
import signal
import threading

from django.core.management.base import BaseCommand
from django.db import OperationalError, connection, connections, reset_queries

from core.system_ops import recover_stale_running_operations_on_scheduler_start, run_scheduler_tick

logger = logging.getLogger(__name__)


class SchedulerLock:
    def __init__(self, acquired, lock_connection=None):
        self.acquired = acquired
        self._connection = lock_connection

    def is_owned(self):
        if self._connection is None:
            return True
        with self._connection.cursor() as cursor:
            cursor.execute(
                "SELECT IS_USED_LOCK(%s), CONNECTION_ID()",
                ["coal_shipments_scheduler"],
            )
            row = cursor.fetchone()
        return bool(row and row[0] is not None and row[0] == row[1])

    def release(self):
        if self._connection is None:
            return
        try:
            try:
                with self._connection.cursor() as cursor:
                    cursor.execute("SELECT RELEASE_LOCK(%s)", ["coal_shipments_scheduler"])
                    row = cursor.fetchone()
                if not row or row[0] != 1:
                    logger.warning("Scheduler advisory lock was not released cleanly: %s", row)
            except OperationalError:
                logger.warning("Scheduler advisory lock release failed; closing connection.", exc_info=True)
        finally:
            self._connection.close()
            self._connection = None


def acquire_scheduler_lock(vendor=None):
    vendor = vendor or connection.vendor
    if vendor != "mysql":
        return SchedulerLock(acquired=True)
    lock_connection = connections["default"].copy(alias="scheduler_lock")
    try:
        lock_connection.ensure_connection()
        with lock_connection.cursor() as cursor:
            cursor.execute("SELECT GET_LOCK(%s, 0)", ["coal_shipments_scheduler"])
            row = cursor.fetchone()
        return SchedulerLock(acquired=bool(row and row[0] == 1), lock_connection=lock_connection)
    except Exception:
        lock_connection.close()
        raise


class Command(BaseCommand):
    help = "Run backup scheduler and queued system operations."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--interval-seconds", type=int, default=60)

    def handle(self, *args, **options):
        interval = max(1, options["interval_seconds"])
        stop_event = threading.Event()

        def request_stop(signum, frame):
            self.stdout.write("Scheduler shutdown requested; stopping after current tick.")
            stop_event.set()

        previous_sigint = signal.getsignal(signal.SIGINT)
        previous_sigterm = signal.getsignal(signal.SIGTERM)
        signal.signal(signal.SIGINT, request_stop)
        signal.signal(signal.SIGTERM, request_stop)

        scheduler_lock = acquire_scheduler_lock()
        if not scheduler_lock.acquired:
            self.stdout.write("Scheduler lock is already held; exiting.")
            return

        try:
            recovered = recover_stale_running_operations_on_scheduler_start()
            if recovered["backup_count"] or recovered["restore_count"]:
                self.stdout.write(
                    "Recovered stale running operations on scheduler start: "
                    f"backup={recovered['backup_count']}, restore={recovered['restore_count']}"
                )
            if recovered.get("recovery_refused"):
                self.stderr.write("Scheduler recovery refused: another scheduler heartbeat is fresh.")
                logger.error("Scheduler recovery refused because scheduler heartbeat is fresh.")
                return
            while not stop_event.is_set():
                try:
                    if not scheduler_lock.is_owned():
                        self.stderr.write("Scheduler advisory lock was lost; exiting.")
                        logger.error("Scheduler advisory lock was lost; exiting.")
                        return
                except OperationalError:
                    self.stderr.write("Scheduler advisory lock check failed; exiting.")
                    logger.exception("Scheduler advisory lock check failed; exiting.")
                    return
                try:
                    result = run_scheduler_tick()
                except Exception:
                    # V17-MED-6: неожиданная ошибка тика не должна ронять демон —
                    # логируем и продолжаем цикл вместо аварийного выхода/crash-loop.
                    logger.exception("Scheduler tick failed; continuing.")
                    self.stderr.write("Scheduler tick failed; continuing.")
                    result = {"claimed": False}
                reset_queries()
                for conn in connections.all():
                    conn.close_if_unusable_or_obsolete()
                if result.get("claimed"):
                    message = (
                        f"{result['kind']} #{result['id']} finished with "
                        f"{result.get('status', 'unknown')}"
                    )
                    if result.get("error"):
                        self.stderr.write(message + f": {result['error']}")
                    else:
                        self.stdout.write(message)
                if options["once"]:
                    return
                stop_event.wait(interval)
        finally:
            scheduler_lock.release()
            signal.signal(signal.SIGINT, previous_sigint)
            signal.signal(signal.SIGTERM, previous_sigterm)
            self.stdout.write("Scheduler stopped.")
