"""Bounded, cooperative admission for orchestrator data, without deleting history.

Only runtime/, runs/ and .orchestration/ are managed. This is an admission
budget, not an OS quota: unrelated programs and unreserved writes can exceed it.
Reservations survive crashes and have no automatic expiry. Callers must reserve
their worst-case additional bytes, including SQLite WAL/temp/export files, and
release only when the writer has definitely stopped.
"""
from contextlib import contextmanager
import json
import math
import os
from pathlib import Path
import stat
import uuid

from usage_guard import file_lock


GIB = 1024 ** 3
MIB = 1024 ** 2


class StorageLimitError(RuntimeError):
    """Storage cannot safely admit an optional write or job."""


def _integer(value, label, minimum=0):
    if (isinstance(value, bool) or not isinstance(value, int)
            or value < minimum or value > (1 << 63) - 1):
        raise ValueError(label + ' must be a bounded integer of at least ' + str(minimum))
    return value


class StorageBudget:
    """Cross-process admission including held allocations and recovery headroom.

    ``check(brain_bytes=N, total_bytes=M)`` adds N bytes to both budgets and
    M additional non-brain bytes to the total budget. ``throttle_fraction=1``
    disables the early optional-write hold, while preserving both hard caps.
    """

    def __init__(self, root, brain_limit_bytes=GIB, total_limit_bytes=2 * GIB,
                 recovery_reserve_bytes=16 * MIB, throttle_fraction=0.8,
                 max_reservations=1024, max_state_bytes=256 * 1024,
                 max_scan_entries=1_000_000):
        self.root = Path(os.path.abspath(os.fspath(root)))
        self.brain_limit_bytes = _integer(brain_limit_bytes, 'Brain limit', 1)
        self.total_limit_bytes = _integer(total_limit_bytes, 'Total limit', 1)
        self.recovery_reserve_bytes = _integer(recovery_reserve_bytes, 'Recovery reserve')
        if self.brain_limit_bytes > self.total_limit_bytes:
            raise ValueError('Brain limit must not exceed total limit')
        if self.recovery_reserve_bytes >= self.brain_limit_bytes:
            raise ValueError('Recovery reserve must be smaller than both budgets')
        if (isinstance(throttle_fraction, bool)
                or not isinstance(throttle_fraction, (float, int))
                or not math.isfinite(throttle_fraction)
                or not 0 < throttle_fraction <= 1):
            raise ValueError('Throttle fraction must be greater than zero and at most one')
        self.throttle_fraction = throttle_fraction
        self.max_reservations = _integer(max_reservations, 'Reservation count', 1)
        self.max_state_bytes = _integer(max_state_bytes, 'Reservation state limit', 64)
        self.max_scan_entries = _integer(max_scan_entries, 'Scan entry limit', 1)
        self.runtime = self.root / 'runtime'
        self.brain = self.runtime / 'brain'
        self.path = self.runtime / 'storage-reservations.json'
        self.lock_path = self.runtime / 'storage-budget.lock'
        try:
            self._safe_ancestors(self.runtime)
            self.runtime.mkdir(parents=True, exist_ok=True)
            self._safe_ancestors(self.runtime)
        except OSError:
            raise StorageLimitError('Managed storage cannot be accessed safely') from None

    @staticmethod
    def _safe_path(path, missing_ok=False):
        try:
            info = path.lstat()
        except FileNotFoundError:
            if missing_ok:
                return None
            raise StorageLimitError('Managed storage changed during inspection') from None
        # Reparse-point rejection also covers Windows junctions on Python
        # versions where Path.is_junction is not yet available.
        junction = getattr(path, 'is_junction', None)
        if (stat.S_ISLNK(info.st_mode)
                or bool(getattr(info, 'st_file_attributes', 0)
                        & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400))
                or (junction is not None and junction())):
            raise StorageLimitError('Linked managed storage is not supported')
        if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
            raise StorageLimitError('Managed storage contains an unsupported file type')
        return info

    def _safe_ancestors(self, path):
        for item in reversed((path, *path.parents)):
            self._safe_path(item, missing_ok=True)

    @contextmanager
    def _locked(self):
        try:
            self._safe_ancestors(self.runtime)
            self._safe_path(self.lock_path, missing_ok=True)
            with file_lock(self.lock_path):
                self._safe_ancestors(self.runtime)
                self._safe_path(self.path, missing_ok=True)
                yield
        except (OSError, TimeoutError):
            raise StorageLimitError('Managed storage cannot be accessed safely') from None

    def _load(self):
        if not self.path.exists():
            return {'version': 1, 'reservations': {}}
        try:
            with self.path.open('rb') as handle:
                raw = handle.read(self.max_state_bytes + 1)
            if len(raw) > self.max_state_bytes:
                raise ValueError()
            def unique_object(pairs):
                result = {}
                for key, value in pairs:
                    if key in result:
                        raise ValueError()
                    result[key] = value
                return result
            value = json.loads(raw, object_pairs_hook=unique_object)
            if (not isinstance(value, dict) or set(value) != {'version', 'reservations'}
                    or type(value['version']) is not int or value['version'] != 1):
                raise ValueError()
            reservations = value['reservations']
            if not isinstance(reservations, dict) or len(reservations) > self.max_reservations:
                raise ValueError()
            keys = set()
            for token, item in reservations.items():
                if (not isinstance(token, str) or len(token) != 32
                        or any(char not in '0123456789abcdef' for char in token)
                        or not isinstance(item, dict) or set(item) != {'bytes', 'kind', 'key'}):
                    raise ValueError()
                _integer(item['bytes'], 'Reservation size', 1)
                # A subsequently lowered budget must hold new work while still
                # permitting explicit release of an older, larger allocation.
                if item['kind'] not in ('brain', 'task'):
                    raise ValueError()
                key = item['key']
                if key is not None:
                    if not isinstance(key, str) or not 1 <= len(key) <= 128 or key in keys:
                        raise ValueError()
                    keys.add(key)
            return value
        except (ValueError, TypeError, UnicodeError, RecursionError):
            raise StorageLimitError('Storage reservation state is invalid; repair is required') from None

    def _encode(self, value):
        raw = (json.dumps(value, ensure_ascii=True, separators=(',', ':')) + '\n').encode('utf-8')
        if len(raw) > self.max_state_bytes:
            raise StorageLimitError('Storage reservation state capacity has been reached')
        return raw

    def _write(self, raw):
        temporary = self.path.with_name('storage-reservations.' + uuid.uuid4().hex + '.tmp')
        created = False
        try:
            with temporary.open('xb') as handle:
                created = True
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            self._safe_path(self.path, missing_ok=True)
            os.replace(temporary, self.path)
        finally:
            if created:
                temporary.unlink(missing_ok=True)

    def _scan(self):
        total = brain = count = 0
        pending = [(self.root / name, False) for name in ('runtime', 'runs', '.orchestration')]
        while pending:
            path, in_brain = pending.pop()
            info = self._safe_path(path, missing_ok=True)
            if info is None:
                continue
            count += 1
            if count > self.max_scan_entries:
                raise StorageLimitError('Managed storage inspection limit has been reached')
            in_brain = in_brain or path == self.brain
            if stat.S_ISDIR(info.st_mode):
                with os.scandir(path) as entries:
                    for entry in entries:
                        # Bound both the traversal and the pending queue.
                        if count + len(pending) >= self.max_scan_entries:
                            raise StorageLimitError('Managed storage inspection limit has been reached')
                        pending.append((Path(entry.path), in_brain))
            else:
                total += info.st_size
                if in_brain:
                    brain += info.st_size
        return brain, total

    def _status(self, state, brain_bytes=0, total_bytes=0, metadata_bytes=0):
        brain, total = self._scan()
        records = state['reservations']
        reserved = sum(item['bytes'] for item in records.values())
        brain_reserved = sum(item['bytes'] for item in records.values() if item['kind'] == 'brain')
        projected_brain = brain + brain_reserved + brain_bytes + self.recovery_reserve_bytes
        projected_total = total + reserved + brain_bytes + total_bytes + metadata_bytes + self.recovery_reserve_bytes
        brain_fraction = projected_brain / self.brain_limit_bytes
        total_fraction = projected_total / self.total_limit_bytes
        maximum = max(brain_fraction, total_fraction)
        status = 'full' if maximum >= 1 else 'throttle' if maximum >= self.throttle_fraction else 'ready'
        return {'brain_bytes': brain, 'brain_limit_bytes': self.brain_limit_bytes,
                'total_bytes': total, 'total_limit_bytes': self.total_limit_bytes,
                'reserved_bytes': reserved, 'brain_reserved_bytes': brain_reserved,
                'reservation_count': len(records), 'recovery_reserve_bytes': self.recovery_reserve_bytes,
                'projected_brain_bytes': projected_brain, 'projected_total_bytes': projected_total,
                'brain_fraction': brain_fraction, 'total_fraction': total_fraction,
                'throttle_fraction': self.throttle_fraction, 'status': status,
                'managed_scopes': ['runtime', 'runs', '.orchestration'],
                'enforcement': 'cooperative-admission'}

    def _admit(self, result, check_brain=True):
        if ((check_brain and result['projected_brain_bytes'] > self.brain_limit_bytes)
                or result['projected_total_bytes'] > self.total_limit_bytes):
            raise StorageLimitError('Managed storage limit reached; new optional writes are held')
        if self.throttle_fraction < 1 and (
                (check_brain and result['brain_fraction'] >= self.throttle_fraction)
                or result['total_fraction'] >= self.throttle_fraction):
            raise StorageLimitError('Managed storage headroom is low; new optional writes are held')

    def status(self):
        with self._locked():
            return self._status(self._load())

    def check(self, brain_bytes=0, total_bytes=0):
        _integer(brain_bytes, 'Additional brain size')
        _integer(total_bytes, 'Additional total size')
        with self._locked():
            result = self._status(self._load(), brain_bytes, total_bytes)
            self._admit(result, check_brain=bool(brain_bytes) or not total_bytes)
            return result

    def reserve(self, amount_bytes, kind='task', key=None):
        _integer(amount_bytes, 'Reservation size', 1)
        if kind not in ('task', 'brain'):
            raise ValueError('Reservation kind must be task or brain')
        if key is not None and (not isinstance(key, str) or not 1 <= len(key) <= 128):
            raise ValueError('Reservation key must contain between 1 and 128 characters')
        with self._locked():
            state = self._load()
            records = state['reservations']
            if key is not None:
                for token, item in records.items():
                    if item['key'] == key:
                        if item['kind'] != kind or item['bytes'] != amount_bytes:
                            raise StorageLimitError('Reservation key already has a different allocation')
                        return token
            if len(records) >= self.max_reservations:
                raise StorageLimitError('Storage reservation count has been reached')
            token = uuid.uuid4().hex
            records[token] = {'bytes': amount_bytes, 'kind': kind, 'key': key}
            raw = self._encode(state)
            # Count the complete replacement file while the old file still
            # exists; atomic state writes cannot silently borrow hard-cap bytes.
            result = self._status(state, metadata_bytes=len(raw))
            self._admit(result, check_brain=kind == 'brain')
            self._write(raw)
            return token

    def release(self, token):
        if not isinstance(token, str):
            raise ValueError('Reservation token must be a string')
        with self._locked():
            state = self._load()
            if token not in state['reservations']:
                return False
            del state['reservations'][token]
            # Recovery must remain possible after a writer used its reservation
            # or an external writer exceeded a cooperative budget. This bounded
            # control update is intentionally exempt from optional-write holds.
            self._write(self._encode(state))
            return True

    @contextmanager
    def allocation(self, amount_bytes, kind='brain'):
        token = self.reserve(amount_bytes, kind=kind)
        try:
            yield token
        finally:
            self.release(token)
