from concurrent.futures import ThreadPoolExecutor

import pytest


def test_ids_are_monotonic_unique_and_encode_worker():
    from short_drama.utils.snowflake import SnowflakeGenerator

    generator = SnowflakeGenerator(worker_id=19)
    ids = [generator.next_id() for _ in range(5000)]
    assert ids == sorted(set(ids))
    assert all(((value >> 12) & 1023) == 19 for value in ids)
    assert all(0 < value < 2**63 for value in ids)


def test_generator_is_thread_safe():
    from short_drama.utils.snowflake import SnowflakeGenerator

    generator = SnowflakeGenerator(worker_id=2)
    with ThreadPoolExecutor(max_workers=8) as executor:
        values = list(executor.map(lambda _: generator.next_id(), range(10000)))
    assert len(set(values)) == 10000


def test_process_generator_first_use_is_safe_under_concurrency(monkeypatch):
    import time

    import short_drama.utils.snowflake as module

    real_generator = module.SnowflakeGenerator
    module._generator.cache_clear()

    def slow_start(worker_id):
        time.sleep(0.02)
        return real_generator(worker_id, clock=lambda: real_generator.EPOCH_MS + 100)

    monkeypatch.setattr(module, "SnowflakeGenerator", slow_start)
    try:
        with ThreadPoolExecutor(max_workers=8) as executor:
            values = list(executor.map(lambda _: module.next_id(), range(64)))
        assert len(set(values)) == 64
    finally:
        module._generator.cache_clear()


def test_clock_rollback_fails_closed_without_reusing_ids():
    from short_drama.utils.snowflake import ClockMovedBackwards, SnowflakeGenerator

    now = [SnowflakeGenerator.EPOCH_MS + 100]
    generator = SnowflakeGenerator(worker_id=1, clock=lambda: now[0])
    first = generator.next_id()
    now[0] -= 1
    with pytest.raises(ClockMovedBackwards):
        generator.next_id()
    now[0] += 1
    assert generator.next_id() > first


def test_sequence_overflow_advances_to_next_millisecond():
    from short_drama.utils.snowflake import SnowflakeGenerator

    ticks = iter([SnowflakeGenerator.EPOCH_MS + 100] * 4097 + [SnowflakeGenerator.EPOCH_MS + 101])
    generator = SnowflakeGenerator(worker_id=0, clock=lambda: next(ticks))
    values = [generator.next_id() for _ in range(4097)]
    assert len(set(values)) == 4097
    assert values[-1] >> 22 == 101


@pytest.mark.parametrize("worker", [-1, 1024, True, 1.5])
def test_invalid_worker_id_rejected(worker):
    from short_drama.utils.snowflake import SnowflakeGenerator

    with pytest.raises(ValueError):
        SnowflakeGenerator(worker_id=worker)
